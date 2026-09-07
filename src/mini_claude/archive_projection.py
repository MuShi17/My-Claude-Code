"""Provider and terminal projections for archived tool results.

The canonical event keeps a bounded ``ArtifactRef``.  This module decides
which consumer-visible view is safe for the current moment: complete content
for a newly completed result when the request can hold it, a bounded prefix
when capacity is insufficient, and an actionable placeholder for stale
history.  None of these projections mutate the canonical event.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .archive_capability import (
    ARCHIVE_PREVIEW_CHARS,
    ToolResultArchiveCapability,
)
from .artifact_archive import ArtifactArchiveError
from .runtime_event import canonical_json_bytes


_ERROR_CODES = {
    "artifact_not_found": "not_found",
    "artifact_metadata_error": "metadata_invalid",
    "artifact_integrity_error": "integrity_mismatch",
    "artifact_size_limit": "invalid_range",
}


def _capacity_error(ref: str) -> dict[str, Any]:
    return {
        "kind": "archive_read_error",
        "error_type": "capacity_exhausted",
        "ref": ref,
    }


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


def _bounded_ref_value(value: Any) -> Mapping[str, Any] | None:
    candidate: Any = value
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except (TypeError, ValueError):
            return None
    if not isinstance(candidate, Mapping):
        return None
    if candidate.get("kind") != "bounded_ref":
        return None
    ref = candidate.get("ref")
    if not isinstance(ref, str) or not ref.startswith("artifact:sha256:"):
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


def _value_size(value: Any) -> int:
    try:
        return len(canonical_json_bytes(value))
    except (TypeError, ValueError, OverflowError):
        return len(str(value).encode("utf-8", errors="replace"))


def _messages_size(messages: Sequence[Mapping[str, Any]]) -> int:
    return _value_size(list(messages))


def _fresh_tool_call_ids(messages: Sequence[Mapping[str, Any]]) -> set[str]:
    """Return the latest complete assistant/tool group not superseded by a step.

    Code-injected context (for example a memory injection) may be appended
    after a tool result before its first Provider request.  It does not
    represent a later model step, so it is ignored for first-use purposes;
    ordinary user/assistant content still makes the result stale.
    """

    tool_indices = [
        index for index, message in enumerate(messages) if message.get("role") == "tool"
    ]
    if not tool_indices:
        return set()
    last_tool_index = tool_indices[-1]
    for message in messages[last_tool_index + 1 :]:
        if message.get("role") == "user" and message.get("context_type"):
            continue
        return set()

    index = last_tool_index
    result_ids: set[str] = set()
    while index >= 0 and messages[index].get("role") == "tool":
        value = messages[index].get("tool_call_id")
        if isinstance(value, str) and value:
            result_ids.add(value)
        index -= 1
    if index < 0 or messages[index].get("role") != "assistant":
        return set()
    calls = messages[index].get("tool_calls")
    if not isinstance(calls, list):
        return set()
    call_ids = {
        str(call.get("id"))
        for call in calls
        if isinstance(call, Mapping) and isinstance(call.get("id"), str)
    }
    # A partially rendered group is not eligible for first-use hydration.
    return call_ids if result_ids == call_ids else set()


def _replace_message_content(
    messages: Sequence[Mapping[str, Any]],
    index: int,
    content: Any,
) -> list[dict[str, Any]]:
    result = [dict(message) for message in messages]
    result[index] = {**result[index], "content": content}
    return result


def _fits(
    messages: Sequence[Mapping[str, Any]],
    index: int,
    content: Any,
    budget_bytes: int | None,
    size_fn: Callable[[Sequence[Mapping[str, Any]]], int] | None = None,
) -> bool:
    if budget_bytes is None:
        return True
    candidate = _replace_message_content(messages, index, content)
    measured = size_fn(candidate) if size_fn is not None else _messages_size(candidate)
    return measured <= budget_bytes


def _capacity_rescue(
    capability: ToolResultArchiveCapability,
    ref: str,
    page: Mapping[str, Any],
    *,
    preview_units: int,
) -> dict[str, Any]:
    unit = str(page.get("unit", "chars"))
    offset = int(page.get("offset", 0))
    raw_page = page.get("page", "")
    if unit == "bytes":
        # ``ToolResultArchiveCapability.read`` exposes binary bytes as a
        # bounded base64 string.  Its page metadata remains authoritative for
        # continuation: the encoded string length is not the byte count.
        preview = raw_page if isinstance(raw_page, str) else str(raw_page)
        next_offset = int(page.get("next_offset", offset + preview_units))
        returned_units = max(0, next_offset - offset)
    else:
        preview = raw_page if isinstance(raw_page, str) else str(raw_page)
        preview = preview[:preview_units]
        next_offset = offset + len(preview)
        returned_units = len(preview)
    result = capability.placeholder(
        ref,
        preview=preview,
        offset=offset,
        next_offset=next_offset,
        include_metadata=False,
    )
    total_units = page.get("total_units")
    if isinstance(total_units, int) and total_units >= 0:
        result["total_units"] = total_units
        result["omitted_units"] = max(0, total_units - next_offset)
        if unit == "chars":
            result["omitted_chars"] = result["omitted_units"]
        else:
            result["omitted_bytes"] = result["omitted_units"]
    result["unit"] = unit
    if unit == "bytes":
        # ``placeholder`` computes ``preview_chars`` from the base64 text;
        # that field would be a misleading continuation measure for bytes.
        result.pop("preview_chars", None)
        result["preview_bytes"] = returned_units
    result["has_more"] = bool(page.get("has_more", True))
    return result


def _minimal_placeholder(
    capability: ToolResultArchiveCapability,
    ref: str,
) -> dict[str, Any]:
    return capability.placeholder(ref, include_metadata=False)


def project_archived_tool_results(
    messages: Sequence[Mapping[str, Any]],
    capability: ToolResultArchiveCapability | None,
    *,
    budget_bytes: int | None = None,
    preview_chars: int = ARCHIVE_PREVIEW_CHARS,
    size_fn: Callable[[Sequence[Mapping[str, Any]]], int] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Project neutral tool-result messages for the next Provider request.

    The latest complete assistant/tool group is the only first-use group.
    Every archived result outside that group is stale, even if its artifact is
    still available.  The input is copied so canonical replay remains the
    source of truth.
    """

    # Only replace the top-level ``content`` field below.  A shallow message
    # copy is deliberate: replay can contain immutable provider argument
    # wrappers (for example mappingproxy/FrozenDict) that are not deepcopyable.
    projected = [dict(message) for message in messages]
    if capability is None:
        for index, message in enumerate(projected):
            parsed = _bounded_ref_value(message.get("content"))
            if parsed is None:
                continue
            replacement = _archive_error(
                str(parsed["ref"]),
                RuntimeError("ArchiveRead capability is unavailable"),
            )
            preview = parsed.get("preview", parsed.get("inline"))
            if isinstance(preview, str):
                replacement["preview"] = preview[:preview_chars]
            if _fits(projected, index, replacement, budget_bytes, size_fn):
                message["content"] = replacement
                continue
            if isinstance(preview, str):
                for length in (2_000, 1_000, 512, 256, 128, 64, 0):
                    candidate = dict(replacement)
                    if length:
                        candidate["preview"] = preview[:length]
                    else:
                        candidate.pop("preview", None)
                    if _fits(projected, index, candidate, budget_bytes, size_fn):
                        message["content"] = candidate
                        break
                else:
                    message["content"] = _capacity_error(str(parsed["ref"]))
            else:
                message["content"] = _capacity_error(str(parsed["ref"]))
        return tuple(projected)
    first_use_ids = _fresh_tool_call_ids(projected)

    for index, message in enumerate(projected):
        if message.get("role") != "tool":
            continue
        parsed = _bounded_ref_value(message.get("content"))
        if parsed is None:
            continue
        ref = str(parsed["ref"])
        try:
            # Legacy refs do not carry session/run metadata.  Registration is
            # scoped to this replay capability; metadata-bearing refs still
            # pass their independent session/lineage checks below.
            capability.register_ref(ref)
            metadata = capability.inspect(ref)
            artifact_size = int(metadata.get("size_bytes", 0))
            tool_call_id = message.get("tool_call_id")
            if tool_call_id not in first_use_ids:
                placeholder = _minimal_placeholder(capability, ref)
                projected[index]["content"] = (
                    placeholder
                    if _fits(projected, index, placeholder, budget_bytes, size_fn)
                    else _capacity_error(ref)
                )
                continue

            full_value: Any | None = None
            if budget_bytes is None or artifact_size <= max(0, budget_bytes):
                full_value = capability.materialize(ref)
                if _fits(projected, index, full_value, budget_bytes, size_fn):
                    projected[index]["content"] = full_value
                    continue

            page = capability.preview(ref, limit=preview_chars)
            rescue = _capacity_rescue(
                capability,
                ref,
                page,
                preview_units=preview_chars,
            )
            if _fits(projected, index, rescue, budget_bytes, size_fn):
                projected[index]["content"] = rescue
                continue

            # Reduce only the prefix. If even the actionable envelope does not
            # fit, return a bounded failure instead of a successful ref that
            # cannot be recovered from this request.
            page_text = page.get("page")
            if isinstance(page_text, str) and page.get("unit") in {"chars", "bytes"}:
                for length in (2_000, 1_000, 512, 256, 128, 64, 0):
                    if length == 0:
                        candidate = _minimal_placeholder(capability, ref)
                    else:
                        if page.get("unit") == "chars":
                            short_page = dict(page)
                            short_page["page"] = page_text[:length]
                            short_page["next_offset"] = int(page.get("offset", 0)) + min(
                                length, len(page_text)
                            )
                            short_page["has_more"] = True
                        else:
                            # Re-read binary pages by byte limit.  Slicing a
                            # base64 string would corrupt the unit/offset
                            # contract and could skip bytes on continuation.
                            short_page = capability.preview(ref, limit=length)
                        candidate = _capacity_rescue(
                            capability,
                            ref,
                            short_page,
                            preview_units=length,
                        )
                    if _fits(projected, index, candidate, budget_bytes, size_fn):
                        projected[index]["content"] = candidate
                        break
                else:
                    projected[index]["content"] = _capacity_error(ref)
            else:
                projected[index]["content"] = _capacity_error(ref)
        except (ArtifactArchiveError, RuntimeError, ValueError, TypeError) as error:
            projected[index]["content"] = _archive_error(ref, error)

    return tuple(projected)


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
    "format_terminal_tool_result",
    "project_archived_tool_results",
    "project_terminal_tool_result",
]
