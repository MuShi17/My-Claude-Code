"""Provider and terminal projections for archived tool results.

The canonical event keeps the complete safe result.  This module decides
which consumer-visible view is safe for the current moment: complete content
for a newly completed result when the request can hold it, a bounded prefix
when capacity is insufficient, and an actionable placeholder for stale
history.  Legacy bounded references remain readable.  None of these
projections mutate the canonical event.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .archive_capability import (
    ARCHIVE_READ_TOOL_NAME,
    ARCHIVE_READ_MAX_LIMIT,
    ARCHIVE_PREVIEW_CHARS,
    ToolResultArchiveCapability,
)
from .artifact_archive import (
    ArtifactArchiveError,
    ArtifactIntegrityError,
    is_artifact_ref,
)
from .tool_result import (
    PRUNE_MAX_ESTIMATED_TOKENS,
    PRUNE_MIN_SUPERSESSION_TOKENS,
    PRUNE_PROTECTED_TURN_COUNT,
)


@dataclass(frozen=True, slots=True)
class ArchiveProjectionResult:
    """Immutable outcome of one neutral archive-pruning pass."""

    messages: tuple[dict[str, Any], ...]
    projection_phase: str = "ordinary"
    emergency_used: bool = False
    request_size_bytes: int | None = None
    request_budget_bytes: int | None = None
    diagnostics: tuple[Any, ...] = ()


_ERROR_CODES = {
    "artifact_not_found": "not_found",
    "artifact_metadata_error": "metadata_invalid",
    "artifact_integrity_error": "integrity_mismatch",
    "artifact_size_limit": "invalid_range",
}


_ARCHIVE_READ_INSTRUCTIONS_MAX_CHARS = 2_048
_ARCHIVE_ERROR_DETAIL_MAX_CHARS = 200
_ARCHIVE_PAGE_ENCODING_MAX_CHARS = 64
_ARCHIVE_MIME_TYPE_MAX_CHARS = 128
_ARCHIVE_SCOPE_FIELD_MAX_CHARS = 64
_ARCHIVE_PAGE_ALLOWED_FIELDS = frozenset(
    {
        "kind",
        "ref",
        "sha256",
        "size_bytes",
        "mime_type",
        "encoding",
        "scope",
        "redaction_version",
        "artifact_id",
        "page",
        "page_encoding",
        "read_instructions",
        "offset",
        "next_offset",
        "total_units",
        "unit",
        "has_more",
    }
)
_ARCHIVE_ERROR_ALLOWED_FIELDS = frozenset(
    {
        "kind",
        "error_type",
        "message",
        "ref",
        "detail",
        "read_instructions",
        "preview",
        "operation",
        "offset",
        "unit",
    }
)
_ARCHIVE_OPERATIONS = frozenset({"inspect", "read", "query"})


def _capacity_error(
    ref: str,
    *,
    message: str = "tool result does not fit the current Provider capacity",
    capability: ToolResultArchiveCapability | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "archive_read_error",
        "error_type": "capacity_exhausted",
        "message": message,
        "ref": ref,
    }
    if capability is not None and is_artifact_ref(ref):
        result["read_instructions"] = capability.read_instructions(
            ref,
            offset=offset,
            limit=limit,
        )
    return result


def _archive_error(ref: str, error: BaseException) -> dict[str, Any]:
    code = _ERROR_CODES.get(
        str(getattr(error, "code", "")),
        str(getattr(error, "code", "capability_unavailable")),
    )
    if code not in {
        "not_found",
        "metadata_invalid",
        "integrity_mismatch",
        "invalid_range",
        "archive_store_closed",
        "session_mismatch",
        "scope_denied",
        "capability_unavailable",
    }:
        code = "capability_unavailable"
    messages = {
        "not_found": "archived artifact was not found",
        "metadata_invalid": "archived artifact metadata is invalid",
        "integrity_mismatch": "archived artifact failed integrity validation",
        "invalid_range": "archive read range is invalid",
        "archive_store_closed": "archive store is closed",
        "session_mismatch": "artifact is outside the current session",
        "scope_denied": "artifact scope is not readable",
        "capability_unavailable": "ArchiveRead capability is unavailable",
    }
    return {
        "kind": "archive_read_error",
        "error_type": code,
        "message": messages[code],
        "ref": ref,
    }


def _capability_unavailable() -> dict[str, Any]:
    return {
        "kind": "archive_read_error",
        "error_type": "capability_unavailable",
        "message": "ArchiveRead capability is unavailable",
    }


def _bounded_ref_value(value: Any) -> Mapping[str, Any] | None:
    candidate = _declared_archive_value(value, "bounded_ref")
    if candidate is None:
        return None
    ref = candidate.get("ref")
    if not _valid_artifact_ref(ref):
        return None
    return candidate


def _structured_value(value: Any) -> Mapping[str, Any] | None:
    candidate: Any = value
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except (TypeError, ValueError):
            return None
    return candidate if isinstance(candidate, Mapping) else None


def _declared_archive_value(
    value: Any,
    kind: str,
) -> Mapping[str, Any] | None:
    candidate = _structured_value(value)
    if candidate is None or candidate.get("kind") != kind:
        return None
    return candidate


def _valid_artifact_ref(value: Any) -> bool:
    """Validate the complete ref shape before echoing it in an error."""

    if not is_artifact_ref(value):
        return False
    if value.startswith("artifact:sha256:"):
        digest = value.removeprefix("artifact:sha256:")
    elif value.startswith("artifact:tool-result:"):
        digest = value.removeprefix("artifact:tool-result:")
    else:
        return False
    if len(digest) != 64:
        return False
    try:
        int(digest, 16)
    except ValueError:
        return False
    return True


def _valid_artifact_id(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_sha256(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )


def _invalid_archive_envelope(ref: Any = None) -> dict[str, Any]:
    """Return a bounded error without copying malformed envelope fields."""

    result: dict[str, Any] = {
        "kind": "archive_read_error",
        "error_type": "invalid_archive_read",
        "message": "invalid ArchiveRead result envelope",
    }
    if _valid_artifact_ref(ref):
        result["ref"] = ref
    return result


def _bounded_string(
    value: Any,
    *,
    max_chars: int,
    allow_empty: bool = True,
) -> bool:
    return (
        isinstance(value, str)
        and (allow_empty or bool(value))
        and len(value) <= max_chars
    )


def _validate_bounded_ref_shape(value: Mapping[str, Any]) -> bool:
    """Check only the local, bounded shape of a historical placeholder."""

    ref = value.get("ref")
    if not _valid_artifact_ref(ref):
        return False
    if "sha256" in value and not _valid_sha256(value.get("sha256")):
        return False
    if "size_bytes" in value and not _non_negative_int(value.get("size_bytes")):
        return False
    if "truncated" in value and not isinstance(value.get("truncated"), bool):
        return False
    for field in ("read_instructions",):
        if field in value and not _bounded_string(
            value.get(field),
            max_chars=_ARCHIVE_READ_INSTRUCTIONS_MAX_CHARS,
        ):
            return False
    for field in ("preview", "inline"):
        if field in value and not _bounded_string(
            value.get(field),
            max_chars=ARCHIVE_PREVIEW_CHARS,
        ):
            return False
    for field in ("offset", "next_offset", "total_units", "preview_chars"):
        if field in value and not _non_negative_int(value.get(field)):
            return False
    if "limit" in value and (
        not isinstance(value.get("limit"), int)
        or isinstance(value.get("limit"), bool)
        or not 1 <= value.get("limit") <= ARCHIVE_READ_MAX_LIMIT
    ):
        return False
    return True


def _validate_bounded_ref_claims(
    value: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate or backfill integrity claims on a historical placeholder."""

    if not _validate_bounded_ref_shape(value):
        raise ArtifactIntegrityError("bounded archive placeholder is malformed")
    actual_sha = metadata.get("sha256")
    actual_size = metadata.get("size_bytes")
    if not _valid_sha256(actual_sha) or not _non_negative_int(actual_size):
        raise ArtifactIntegrityError("artifact metadata has invalid integrity claims")

    result = dict(value)
    if "sha256" in result:
        if result["sha256"] != actual_sha:
            raise ArtifactIntegrityError("placeholder sha256 does not match artifact metadata")
    else:
        result["sha256"] = actual_sha
    if "size_bytes" in result:
        if not _non_negative_int(result["size_bytes"]) or result["size_bytes"] != actual_size:
            raise ArtifactIntegrityError("placeholder size does not match artifact metadata")
    else:
        result["size_bytes"] = actual_size
    return result


def _archive_page_is_bounded(
    page: Any,
    *,
    unit: str,
    page_encoding: str | None,
) -> bool:
    """Bound the wire representation, including binary/base64 expansion."""

    if isinstance(page, bytes):
        return len(page) <= ARCHIVE_READ_MAX_LIMIT
    if not isinstance(page, str):
        return False
    if unit == "chars":
        return len(page) <= ARCHIVE_READ_MAX_LIMIT
    if page_encoding == "base64" or (
        page_encoding is None and page.startswith("base64:")
    ):
        encoded = page.removeprefix("base64:")
        # A page returned by ToolResultArchiveCapability always includes the
        # prefix.  Reject malformed padding before it can reach the Provider.
        if page_encoding == "base64" and not page.startswith("base64:"):
            return False
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError, UnicodeError):
            return False
        return len(decoded) <= ARCHIVE_READ_MAX_LIMIT
    try:
        return len(page.encode("utf-8")) <= ARCHIVE_READ_MAX_LIMIT
    except UnicodeError:
        return False


def _archive_page_values_match(
    claimed: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> bool:
    """Compare all provider-visible provenance fields with an authorized read."""

    for field in (
        "ref",
        "sha256",
        "size_bytes",
        "offset",
        "next_offset",
        "total_units",
        "unit",
        "has_more",
    ):
        if claimed.get(field) != actual.get(field):
            return False

    claimed_page = claimed.get("page")
    actual_page = actual.get("page")
    if claimed_page != actual_page:
        if not isinstance(claimed_page, bytes) or not isinstance(actual_page, str):
            return False
        if actual_page.startswith("base64:"):
            if "base64:" + base64.b64encode(claimed_page).decode("ascii") != actual_page:
                return False
        else:
            encoding = claimed.get("page_encoding") or actual.get("page_encoding") or "utf-8"
            try:
                if claimed_page.decode(str(encoding), errors="replace") != actual_page:
                    return False
            except (LookupError, TypeError):
                return False

    if "page_encoding" in claimed and claimed.get("page_encoding") != actual.get("page_encoding"):
        return False
    for field in (
        "mime_type",
        "encoding",
        "scope",
        "redaction_version",
        "artifact_id",
    ):
        if field in claimed and claimed.get(field) != actual.get(field):
            return False
    return True


def _validate_archive_page(
    value: Any,
    capability: ToolResultArchiveCapability | None,
) -> Any:
    """Validate a bounded page and, when possible, prove it came from the ref."""

    structured = _declared_archive_value(value, "archive_page")
    if structured is None:
        return _invalid_archive_envelope(
            structured.get("ref") if structured is not None else None
        )
    ref = structured.get("ref")
    sha256 = structured.get("sha256")
    size_bytes = structured.get("size_bytes")
    offset = structured.get("offset")
    next_offset = structured.get("next_offset")
    total_units = structured.get("total_units")
    unit = structured.get("unit")
    page_encoding = structured.get("page_encoding")
    if not set(structured).issubset(_ARCHIVE_PAGE_ALLOWED_FIELDS):
        return _invalid_archive_envelope(ref)
    if (
        not _valid_artifact_ref(ref)
        or not _valid_sha256(sha256)
        or not _non_negative_int(size_bytes)
        or not isinstance(structured.get("page"), (str, bytes))
        or not _non_negative_int(offset)
        or not _non_negative_int(next_offset)
        or not _non_negative_int(total_units)
        or next_offset < offset
        or total_units < next_offset
        or next_offset - offset > ARCHIVE_READ_MAX_LIMIT
        or not isinstance(unit, str)
        or unit not in {"chars", "bytes"}
        or not isinstance(structured.get("has_more"), bool)
        or structured.get("has_more") != (next_offset < total_units)
        or not _archive_page_is_bounded(
            structured.get("page"),
            unit=unit,
            page_encoding=page_encoding if isinstance(page_encoding, str) else None,
        )
    ):
        return _invalid_archive_envelope(ref)
    for field, max_chars in (
        ("read_instructions", _ARCHIVE_READ_INSTRUCTIONS_MAX_CHARS),
        ("mime_type", _ARCHIVE_MIME_TYPE_MAX_CHARS),
        ("encoding", _ARCHIVE_PAGE_ENCODING_MAX_CHARS),
        ("scope", _ARCHIVE_SCOPE_FIELD_MAX_CHARS),
        ("redaction_version", _ARCHIVE_SCOPE_FIELD_MAX_CHARS),
    ):
        if field in structured and not _bounded_string(
            structured.get(field),
            max_chars=max_chars,
            allow_empty=False,
        ):
            return _invalid_archive_envelope(ref)
    if "artifact_id" in structured and not _valid_artifact_id(structured.get("artifact_id")):
        return _invalid_archive_envelope(ref)
    if "page_encoding" in structured and not _bounded_string(
        page_encoding,
        max_chars=_ARCHIVE_PAGE_ENCODING_MAX_CHARS,
        allow_empty=False,
    ):
        return _invalid_archive_envelope(ref)
    if capability is not None:
        try:
            capability.register_ref(ref)
            capability.inspect(
                ref,
                expected_sha256=sha256,
                expected_size_bytes=size_bytes,
            )
            span = next_offset - offset
            actual = capability.read(
                ref,
                offset=offset,
                limit=span if span > 0 else 1,
                expected_sha256=sha256,
                expected_size_bytes=size_bytes,
            )
        except (ArtifactArchiveError, RuntimeError, ValueError, TypeError) as error:
            return _archive_error(ref, error)
        if not _archive_page_values_match(structured, actual):
            return _archive_error(
                ref,
                ArtifactIntegrityError("ArchiveRead page does not match artifact"),
            )
    return value


def _validate_archive_error(value: Any) -> Any:
    """Validate a bounded ArchiveRead error without trusting arbitrary fields."""

    structured = _declared_archive_value(value, "archive_read_error")
    if structured is None:
        return _invalid_archive_envelope(
            structured.get("ref") if structured is not None else None
        )
    if not set(structured).issubset(_ARCHIVE_ERROR_ALLOWED_FIELDS):
        return _invalid_archive_envelope(structured.get("ref"))
    error_type = structured.get("error_type")
    message = structured.get("message")
    if (
        not isinstance(error_type, str)
        or not error_type
        or len(error_type) > 96
        or not isinstance(message, str)
        or not message
        or len(message) > 256
    ):
        return _invalid_archive_envelope(structured.get("ref"))
    if "ref" in structured:
        ref = structured.get("ref")
        if ref not in (None, "") and not _valid_artifact_ref(ref):
            return _invalid_archive_envelope(ref)
    if "detail" in structured and not _bounded_string(
        structured.get("detail"),
        max_chars=_ARCHIVE_ERROR_DETAIL_MAX_CHARS,
    ):
        return _invalid_archive_envelope(structured.get("ref"))
    if "read_instructions" in structured and not _bounded_string(
        structured.get("read_instructions"),
        max_chars=_ARCHIVE_READ_INSTRUCTIONS_MAX_CHARS,
    ):
        return _invalid_archive_envelope(structured.get("ref"))
    if "preview" in structured and not _bounded_string(
        structured.get("preview"),
        max_chars=ARCHIVE_PREVIEW_CHARS,
    ):
        return _invalid_archive_envelope(structured.get("ref"))
    if "operation" in structured and (
        not _bounded_string(
            structured.get("operation"),
            max_chars=64,
            allow_empty=False,
        )
        or structured.get("operation") not in _ARCHIVE_OPERATIONS
    ):
        return _invalid_archive_envelope(structured.get("ref"))
    if "offset" in structured and not _non_negative_int(structured.get("offset")):
        return _invalid_archive_envelope(structured.get("ref"))
    if "unit" in structured and (
        not isinstance(structured.get("unit"), str)
        or structured.get("unit") not in {"chars", "bytes"}
    ):
        return _invalid_archive_envelope(structured.get("ref"))
    return value


def _validate_archive_envelope(
    value: Any,
    capability: ToolResultArchiveCapability | None,
) -> Any:
    structured = _structured_value(value)
    kind = structured.get("kind") if structured is not None else None
    if kind == "archive_page":
        return _validate_archive_page(value, capability)
    if kind == "archive_read_error":
        return _validate_archive_error(value)
    if kind == "bounded_ref":
        return _existing_archive_projection(value, capability)
    return _invalid_archive_envelope(
        structured.get("ref") if structured is not None else None
    )


def _ensure_read_instructions(
    value: Mapping[str, Any],
    capability: ToolResultArchiveCapability,
    ref: str,
) -> dict[str, Any]:
    """Preserve a historical placeholder, filling only a missing hint.

    Historical placeholders can outlive the request that produced them.  If
    their hint was omitted by an older writer, continue from the furthest
    recorded page boundary instead of silently restarting at offset zero.
    """

    result = dict(value)
    instructions = result.get("read_instructions")
    if not isinstance(instructions, str) or not instructions:
        def non_negative_int(candidate: Any) -> int | None:
            if isinstance(candidate, bool) or not isinstance(candidate, int):
                return None
            return candidate if candidate >= 0 else None

        next_offset = non_negative_int(result.get("next_offset"))
        offset = non_negative_int(result.get("offset"))
        continuation_offset = (
            next_offset if next_offset is not None
            else offset if offset is not None
            else 0
        )
        existing_limit = result.get("limit")
        limit = (
            existing_limit
            if (
                isinstance(existing_limit, int)
                and not isinstance(existing_limit, bool)
                and 1 <= existing_limit <= capability.max_limit
            )
            else None
        )
        result["read_instructions"] = capability.read_instructions(
            ref,
            offset=continuation_offset,
            limit=limit,
        )
    return result


def _tool_names_by_call_id(
    messages: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Resolve tool names without relying on provider-specific message shape."""

    names: dict[str, str] = {}
    for message in messages:
        if message.get("role") != "assistant":
            continue
        calls = message.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            call_id = call.get("id")
            name = call.get("name")
            if isinstance(call_id, str) and call_id and isinstance(name, str) and name:
                names[call_id] = name

    # Neutral replay normally has the assistant call metadata.  The fallback
    # keeps the projector useful for already-normalized tool messages that
    # retain their provider-neutral ``name`` field but no longer carry calls.
    for message in messages:
        if message.get("role") != "tool":
            continue
        call_id = message.get("tool_call_id")
        name = message.get("name")
        if (
            isinstance(call_id, str)
            and call_id
            and isinstance(name, str)
            and name
        ):
            names.setdefault(call_id, name)
    return names


def _archive_read_capacity_error(
    value: Any,
    capability: ToolResultArchiveCapability | None = None,
) -> dict[str, Any]:
    """Return a bounded, non-recursive error for an oversized ArchiveRead page."""

    structured = _structured_value(value)
    ref = ""
    if structured is not None:
        candidate_ref = structured.get("ref")
        if is_artifact_ref(candidate_ref):
            ref = candidate_ref
    offset = 0
    limit: int | None = None
    if structured is not None:
        candidate_offset = structured.get("offset")
        if (
            isinstance(candidate_offset, int)
            and not isinstance(candidate_offset, bool)
            and candidate_offset >= 0
        ):
            offset = candidate_offset
        candidate_limit = structured.get("limit")
        if (
            isinstance(candidate_limit, int)
            and not isinstance(candidate_limit, bool)
            and candidate_limit > 0
        ):
            limit = candidate_limit
    result = _capacity_error(
        ref,
        message=(
            "ArchiveRead result does not fit the current Provider capacity; "
            "retry with a smaller limit"
        ),
        capability=capability,
        offset=offset,
        limit=limit,
    )
    if structured is not None:
        operation = structured.get("operation")
        if isinstance(operation, str) and operation:
            result["operation"] = operation
        offset = structured.get("offset")
        if isinstance(offset, int) and not isinstance(offset, bool) and offset >= 0:
            result["offset"] = offset
        unit = structured.get("unit")
        if unit in {"chars", "bytes"}:
            result["unit"] = unit
    return result


_PROJECTION_DIAGNOSTIC_MESSAGES = {
    "archive_write_failed": "tool result was kept inline because archive publication failed",
    "archive_identity_unavailable": "tool result was kept inline because archive identity is incomplete",
    "replay_metadata_unavailable": "archive pruning skipped because replay metadata is unavailable",
    "synthetic_message_unclassifiable": "synthetic replay message is not eligible for archive pruning",
    "active_emergency_used": "active tool-result emergency pruning was used for this Provider request",
}


def _projection_diagnostic(
    code: str,
    meta: ReplayMessageMeta | None = None,
    *,
    severity: str = "warning",
) -> Any:
    """Create a bounded, payload-free diagnostic for an archive pass."""

    # Import lazily to avoid the projections package importing provider_context
    # while archive_projection itself is being initialized.
    from .projections.base import ProjectionDiagnostic

    return ProjectionDiagnostic(
        code,
        _PROJECTION_DIAGNOSTIC_MESSAGES.get(code, "archive projection diagnostic"),
        severity,
        meta.runtime_event_id if meta else None,
        meta.run_id if meta else None,
        meta.tool_call_id if meta else None,
    )


def _recent_turns(
    metadata: Sequence[ReplayMessageMeta],
) -> set[str]:
    """Return the newest turns by first canonical ordinal appearance."""

    ordered: list[str] = []
    seen: set[str] = set()
    for item in sorted(
        (meta for meta in metadata if meta.turn_id and meta.canonical_ordinal is not None),
        key=lambda meta: (int(meta.canonical_ordinal or 0), meta.runtime_event_id or ""),
    ):
        assert item.turn_id is not None
        if item.turn_id not in seen:
            ordered.append(item.turn_id)
            seen.add(item.turn_id)
    return set(ordered[-PRUNE_PROTECTED_TURN_COUNT:])


def _is_existing_archive_value(value: Any) -> bool:
    structured = _structured_value(value)
    if structured is None:
        return False
    if structured.get("kind") in {"archive_page", "archive_read_error"}:
        return True
    # ``kind=bounded_ref`` is a declaration, not proof that the ref is valid.
    # Keep malformed declarations out of the raw candidate path so a bad
    # placeholder can never be archived recursively.
    return _declared_archive_value(structured, "bounded_ref") is not None


def _active_same_step(left: ReplayMessageMeta, right: ReplayMessageMeta) -> bool:
    return bool(
        left.has_supersession_identity
        and right.has_supersession_identity
        and left.turn_id == right.turn_id
        and left.run_id == right.run_id
        and left.step_ordinal is not None
        and right.step_ordinal is not None
    )


def _is_newer(left: ReplayMessageMeta, right: ReplayMessageMeta) -> bool:
    """Return whether ``right`` is a later canonical step/event than ``left``."""

    # Canonical ordinals distinguish calls inside one step, but they do not
    # establish a semantic update relationship.  Only a strictly later
    # completed step may supersede an earlier result; parallel calls in the
    # same invocation therefore remain visible.
    if left.step_ordinal is None or right.step_ordinal is None:
        return False
    return right.step_ordinal > left.step_ordinal


_SEMANTIC_TOOL_NAMES = frozenset(
    {"read_file", "list_files", "grep_search", "run_shell"}
)


def _read_range_covers(
    old: ReplayMessageMeta,
    new: ReplayMessageMeta,
) -> bool:
    if old.range_identity is None or new.range_identity is None:
        return False
    if len(old.range_identity) != 3 or len(new.range_identity) != 3:
        return False
    old_path, old_start, old_end = old.range_identity
    new_path, new_start, new_end = new.range_identity
    if old_path != new_path or not isinstance(old_start, int) or not isinstance(new_start, int):
        return False
    if new_start > old_start:
        return False
    if old_end is None:
        return new_end is None
    return new_end is None or (
        isinstance(new_end, int) and isinstance(old_end, int) and new_end >= old_end
    )


def _semantic_superseded_indices(
    projected: Sequence[Mapping[str, Any]],
    metadata: Sequence[ReplayMessageMeta],
    *,
    active_turn_id: str | None,
) -> set[int]:
    """Find active historical results replaced by newer, equivalent evidence."""

    if not active_turn_id:
        return set()
    candidates: set[int] = set()
    tool_indices = [
        index
        for index, message in enumerate(projected)
        if message.get("role") == "tool"
        and index < len(metadata)
        and metadata[index].has_supersession_identity
        and metadata[index].turn_id == active_turn_id
        and not _is_existing_archive_value(message.get("content"))
    ]
    for position, old_index in enumerate(tool_indices):
        old = metadata[old_index]
        if old.estimated_tokens is None or old.estimated_tokens < PRUNE_MIN_SUPERSESSION_TOKENS:
            continue
        for new_index in tool_indices[position + 1 :]:
            new = metadata[new_index]
            if not _active_same_step(old, new) or not _is_newer(old, new):
                continue
            if (
                old.tool_name not in _SEMANTIC_TOOL_NAMES
                or new.tool_name not in _SEMANTIC_TOOL_NAMES
                or not old.semantic_input_complete
                or not new.semantic_input_complete
            ):
                continue
            if old.success is None or new.success is None:
                continue
            exact_duplicate = (
                old.tool_name == new.tool_name
                and old.arguments_digest == new.arguments_digest
                and old.success == new.success
                and old.body_sha256 == new.body_sha256
            )
            read_coverage = (
                old.tool_name == "read_file"
                and new.tool_name == "read_file"
                and old.success is True
                and new.success is True
                and _read_range_covers(old, new)
            )
            snapshot = (
                old.snapshot_identity is not None
                and new.snapshot_identity == old.snapshot_identity
                and old.success is True
                and new.success is True
            )
            if exact_duplicate or read_coverage or snapshot:
                candidates.add(old_index)
                break
    return candidates


def _prune_candidate_indices(
    projected: Sequence[Mapping[str, Any]],
    metadata: Sequence[ReplayMessageMeta],
    *,
    active_turn_id: str | None,
    include_latest_active: bool,
) -> set[int]:
    if len(metadata) != len(projected):
        return set()
    recent_turns = _recent_turns(metadata)
    result: set[int] = set()
    active: list[int] = []
    for index, (message, meta) in enumerate(zip(projected, metadata, strict=True)):
        if message.get("role") != "tool" or _is_existing_archive_value(message.get("content")):
            continue
        if not meta.has_active_identity or meta.estimated_tokens is None:
            continue
        if meta.estimated_tokens <= PRUNE_MAX_ESTIMATED_TOKENS:
            continue
        if active_turn_id is not None and meta.turn_id == active_turn_id:
            active.append(index)
            continue
        # A stale result needs a verifiable ordinal and turn.  Unknown or
        # synthetic messages are intentionally fail-open.
        if meta.canonical_ordinal is not None and meta.turn_id not in recent_turns:
            result.add(index)

    if active:
        step_values = [
            metadata[index].step_ordinal or metadata[index].canonical_ordinal or 0
            for index in active
        ]
        newest_step = max(step_values)
        for index in active:
            step = metadata[index].step_ordinal or metadata[index].canonical_ordinal or 0
            if step < newest_step or include_latest_active:
                result.add(index)

    result.update(
        _semantic_superseded_indices(
            projected,
            metadata,
            active_turn_id=active_turn_id,
        )
    )
    return result


def _missing_identity_would_block_prune(
    meta: ReplayMessageMeta,
    *,
    active_turn_id: str | None,
    recent_turns: set[str],
) -> bool:
    """Detect an otherwise eligible result whose identity is incomplete."""

    if meta.estimated_tokens is None or meta.estimated_tokens <= PRUNE_MAX_ESTIMATED_TOKENS:
        return False
    # Once a result is known to be oversized, an incomplete sidecar is itself
    # useful evidence: the projector must explain why it could not classify
    # the result rather than silently making the caller guess.  This diagnostic
    # does not authorize pruning; complete identity is still required for an
    # archive ref or semantic supersession.
    del active_turn_id, recent_turns
    return meta.identity_state != "complete"


def _existing_archive_projection(
    value: Any,
    capability: ToolResultArchiveCapability | None,
) -> Any:
    """Validate and pass through an already established archive envelope."""

    parsed = _declared_archive_value(value, "bounded_ref")
    if parsed is None:
        return value
    ref = parsed.get("ref")
    if not _valid_artifact_ref(ref):
        return _invalid_archive_envelope(ref)
    ref = str(ref)
    if capability is None:
        result = _archive_error(ref, RuntimeError("ArchiveRead capability is unavailable"))
        preview = parsed.get("preview", parsed.get("inline"))
        if isinstance(preview, str) and len(preview) <= ARCHIVE_PREVIEW_CHARS:
            result["preview"] = preview
        return result
    try:
        capability.register_ref(ref)
        metadata = capability.inspect(ref)
        result = _validate_bounded_ref_claims(parsed, metadata)
    except (ArtifactArchiveError, RuntimeError, ValueError, TypeError) as error:
        return _archive_error(ref, error)
    # Keep an established placeholder byte-for-byte stable apart from a
    # missing continuation instruction from an older writer.
    return _ensure_read_instructions(result, capability, ref)


def project_archived_tool_results_outcome(
    messages: Sequence[Mapping[str, Any]],
    capability: ToolResultArchiveCapability | None,
    *,
    message_metadata: Sequence[ReplayMessageMeta] | None = None,
    active_turn_id: str | None = None,
    include_latest_active: bool = False,
    budget_bytes: int | None = None,
    preview_chars: int = ARCHIVE_PREVIEW_CHARS,
    size_fn: Callable[[Sequence[Mapping[str, Any]]], int] | None = None,
) -> ArchiveProjectionResult:
    """Run one Maka-style stale/active archive projection pass.

    Eligibility is based on one tool result's estimated tokens and canonical
    identity.  Aggregate Provider capacity is measured by the caller after
    this pass; it never mutates an already established placeholder.
    """

    from .projections.replay_metadata import ReplayMessageMeta

    projected = [dict(message) for message in messages]
    preview_chars = max(1, min(int(preview_chars), ARCHIVE_PREVIEW_CHARS))
    tool_names = _tool_names_by_call_id(projected)
    metadata = tuple(message_metadata or ())
    diagnostics: list[Any] = []
    metadata_available = message_metadata is not None and len(metadata) == len(projected)
    if not metadata_available:
        diagnostics.append(_projection_diagnostic("replay_metadata_unavailable"))
        metadata = tuple(ReplayMessageMeta() for _ in projected)

    candidate_indices = _prune_candidate_indices(
        projected,
        metadata,
        active_turn_id=active_turn_id,
        include_latest_active=include_latest_active,
    )
    recent_turns = _recent_turns(metadata)
    if include_latest_active and candidate_indices:
        diagnostics.append(_projection_diagnostic("active_emergency_used"))

    for index, (message, meta) in enumerate(zip(projected, metadata, strict=True)):
        if message.get("role") != "tool":
            continue
        value = message.get("content")
        is_archive_read = (
            meta.tool_name == ARCHIVE_READ_TOOL_NAME
            or tool_names.get(str(message.get("tool_call_id"))) == ARCHIVE_READ_TOOL_NAME
        )
        if _declared_archive_value(value, "bounded_ref") is not None:
            projected[index]["content"] = _existing_archive_projection(
                value, capability
            )
            continue
        if is_archive_read:
            projected[index]["content"] = _validate_archive_envelope(
                value, capability
            )
            continue
        if _is_existing_archive_value(value):
            # ArchiveRead pages/errors are already bounded results.  They are
            # not input to a second archive operation.
            projected[index]["content"] = _validate_archive_envelope(
                value, capability
            )
            continue
        if index not in candidate_indices:
            if meta.identity_state == "synthetic" and value is not None and metadata_available:
                diagnostics.append(_projection_diagnostic("synthetic_message_unclassifiable", meta))
            elif (
                meta.identity_state != "complete"
                and _missing_identity_would_block_prune(
                    meta,
                    active_turn_id=active_turn_id,
                    recent_turns=recent_turns,
                )
            ):
                diagnostics.append(_projection_diagnostic("archive_identity_unavailable", meta))
            continue
        if not meta.has_archive_identity:
            diagnostics.append(_projection_diagnostic("archive_identity_unavailable", meta))
            continue
        if capability is None:
            diagnostics.append(_projection_diagnostic("archive_write_failed", meta))
            continue
        try:
            archived = capability.archive_result(
                value,
                call_id=meta.tool_call_id,
                tool_name=meta.tool_name,
                runtime_event_id=meta.runtime_event_id,
            )
            projected[index]["content"] = capability.placeholder(
                archived.ref,
                include_metadata=False,
            )
        except (ArtifactArchiveError, RuntimeError, ValueError, TypeError) as error:
            # Fail-open: the canonical/raw result remains visible to the final
            # capacity gate.  Never expose a fabricated ref or archive error as
            # a substitute for an unsuccessful archive write.
            del error
            diagnostics.append(_projection_diagnostic("archive_write_failed", meta))

    request_size = None
    if size_fn is not None:
        try:
            request_size = int(size_fn(projected))
        except (TypeError, ValueError, OverflowError):
            request_size = None
    return ArchiveProjectionResult(
        messages=tuple(projected),
        projection_phase="emergency" if include_latest_active else "ordinary",
        emergency_used=bool(include_latest_active and candidate_indices),
        request_size_bytes=request_size,
        request_budget_bytes=budget_bytes,
        diagnostics=tuple(diagnostics),
    )


def project_archived_tool_results(
    messages: Sequence[Mapping[str, Any]],
    capability: ToolResultArchiveCapability | None,
    *,
    message_metadata: Sequence[ReplayMessageMeta] | None = None,
    active_turn_id: str | None = None,
    include_latest_active: bool = False,
    budget_bytes: int | None = None,
    preview_chars: int = ARCHIVE_PREVIEW_CHARS,
    size_fn: Callable[[Sequence[Mapping[str, Any]]], int] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Compatibility wrapper returning only projected neutral messages."""

    return project_archived_tool_results_outcome(
        messages,
        capability,
        message_metadata=message_metadata,
        active_turn_id=active_turn_id,
        include_latest_active=include_latest_active,
        budget_bytes=budget_bytes,
        preview_chars=preview_chars,
        size_fn=size_fn,
    ).messages


def project_terminal_tool_result(
    value: Any,
    capability: ToolResultArchiveCapability | None,
    *,
    preview_chars: int = ARCHIVE_PREVIEW_CHARS,
) -> Any:
    """Render an archived result for the terminal without full hydration."""

    parsed = _bounded_ref_value(value)
    if capability is None or parsed is None:
        return value
    ref = str(parsed["ref"])
    try:
        capability.register_ref(ref)
        metadata = capability.inspect(ref)
        page = capability.preview(ref, limit=preview_chars)
        preview = page.get("page")
        result = {
            "kind": "archived_tool_result",
            "ref": ref,
            "sha256": metadata.get("sha256"),
            "size_bytes": metadata.get("size_bytes"),
            "encoding": metadata.get("encoding"),
            "preview": preview if isinstance(preview, str) else str(preview),
            "truncated": bool(page.get("has_more", True)),
            "offset": page.get("offset", 0),
            "next_offset": page.get("next_offset", 0),
            "unit": page.get("unit", "chars"),
            "read_instructions": capability.read_instructions(
                ref, offset=int(page.get("next_offset", 0))
            ),
        }
        return result
    except (ArtifactArchiveError, RuntimeError, ValueError, TypeError) as error:
        return _archive_error(ref, error)


def format_terminal_tool_result(
    value: Any,
    capability: ToolResultArchiveCapability | None,
    *,
    max_chars: int = 500,
    preview_chars: int = ARCHIVE_PREVIEW_CHARS,
) -> str | None:
    """Format known archive envelopes as content-first terminal text.

    ``None`` means the caller should retain its existing generic renderer.
    Known envelopes are deliberately rendered without JSON escaping so the
    terminal remains useful even when the Provider wire value is JSON text.
    """

    projected = project_terminal_tool_result(
        value,
        capability,
        preview_chars=preview_chars,
    )
    parsed = _structured_value(projected)
    if parsed is None:
        return None
    kind = parsed.get("kind")
    if kind == "archived_tool_result":
        preview = str(parsed.get("preview", ""))
        ref = str(parsed.get("ref", ""))
        next_offset = parsed.get("next_offset", 0)
        instruction = str(parsed.get("read_instructions", ""))
        suffix = (
            f"\n... (archived; next_offset={next_offset})"
            f"\nArchiveRead ref={ref}"
        )
        if instruction:
            suffix += f"\n{instruction}"
        available = max(0, max_chars - len(suffix))
        if len(preview) > available:
            preview = preview[:available]
        return (preview + suffix)[:max_chars]
    if kind == "bounded_ref":
        preview = parsed.get("preview", parsed.get("inline", ""))
        if not isinstance(preview, str):
            preview = str(preview)
        suffix = f"\n... (archived; ArchiveRead unavailable)\nref={parsed.get('ref', '')}"
        available = max(0, max_chars - len(suffix))
        return (preview[:available] + suffix)[:max_chars]
    if kind == "archive_page":
        page = parsed.get("page", parsed.get("content", ""))
        if isinstance(page, bytes):
            page = page.decode("utf-8", errors="replace")
        body = str(page)
        status = (
            f"\n[ArchiveRead offset={parsed.get('offset', 0)}"
            f" next_offset={parsed.get('next_offset', 0)}"
            f" total_units={parsed.get('total_units', 0)}"
            f" unit={parsed.get('unit', 'unknown')}"
            f" has_more={str(bool(parsed.get('has_more', False))).lower()}]"
        )
        available = max(0, max_chars - len(status))
        if len(body) > available:
            body = body[:available]
        return (body + status)[:max_chars]
    if kind == "archive_read_error":
        error_type = str(parsed.get("error_type", "archive_read_error"))
        default_message = (
            "context budget exhausted"
            if error_type == "capacity_exhausted"
            else "archived artifact operation failed"
        )
        message = str(parsed.get("message", default_message))
        return f"Error [{error_type}]: {message}"[:max_chars]
    return None


__all__ = [
    "ArchiveProjectionResult",
    "format_terminal_tool_result",
    "project_archived_tool_results",
    "project_archived_tool_results_outcome",
    "project_terminal_tool_result",
]
