"""Scoped runtime capability for reading archived tool results."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable, Mapping
from typing import Any

from .artifact_archive import (
    ArtifactAccessError,
    ArtifactArchive,
    ArtifactArchiveError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactPage,
    ArtifactRef,
    ArtifactSizeLimitError,
    is_artifact_ref,
)
from .redaction import RedactionPolicy
from .tool_result import MAX_TOOL_RESULT_BYTES, validate_tool_result_size

ARCHIVE_READ_TOOL_NAME = "ArchiveRead"
ARCHIVE_READ_DEFAULT_LIMIT = 6_000
ARCHIVE_READ_MAX_LIMIT = 7_500
ARCHIVE_PREVIEW_CHARS = 4_000
ARCHIVE_ALLOWED_SCOPES = frozenset({"tool-result", "runtime"})
ARCHIVE_QUERY_MAX_FIELDS = 16
ARCHIVE_REWRITE_VERSION = 1
ARCHIVE_READ_INSTRUCTIONS = (
    'This result is archived but still readable. Call ArchiveRead with '
    'operation "inspect" first, or operation "read" with the provided ref, '
    'offset, and limit. Do not use a local file path or Glob to find the archive.'
)


class ArchiveCapabilityError(RuntimeError):
    """A controlled failure while using an archive capability."""

    code = "capability_unavailable"


class ArchiveStoreClosedError(ArchiveCapabilityError):
    code = "archive_store_closed"


class ArchiveReadInputError(ArchiveCapabilityError, ValueError):
    code = "invalid_archive_read"


class ArchiveReadRangeError(ArchiveCapabilityError, ValueError):
    code = "invalid_range"


class ArchiveReadScopeError(ArchiveCapabilityError):
    code = "scope_denied"


class ArchiveReadSessionError(ArchiveCapabilityError):
    code = "session_mismatch"


_ERROR_MESSAGES = {
    "not_found": "archived artifact was not found",
    "metadata_invalid": "archived artifact metadata is invalid",
    "integrity_mismatch": "archived artifact failed integrity validation",
    "invalid_range": "archive read range is invalid",
    "artifact_not_found": "archived artifact was not found",
    "artifact_integrity_error": "archived artifact failed integrity validation",
    "artifact_metadata_error": "archived artifact metadata is invalid",
    "artifact_size_limit": "archive read range is outside the bounded limit",
    "artifact_archive_error": "archived artifact could not be read",
    "artifact_store_closed": "archive store is closed",
    "invalid_archive_read": "invalid ArchiveRead request",
    "scope_denied": "artifact scope is not readable by this capability",
    "session_mismatch": "artifact is outside the current session or lineage",
    "limit_exceeded": "ArchiveRead limit exceeds the bounded maximum",
    "not_queryable": "requested archive metadata is not queryable",
    "capability_unavailable": "ArchiveRead capability is unavailable",
}


def _safe_message(code: str) -> str:
    return _ERROR_MESSAGES.get(code, "archived artifact operation failed")


def _is_store_closed(store: Any) -> bool:
    if store is None:
        return False
    if bool(getattr(store, "_closed", False)):
        return True
    try:
        _ = store.connection
    except Exception as error:
        error_code = getattr(error, "code", None)
        if error_code == "closed" or "store is closed" in str(error).lower():
            return True
    return False


def _error_result(code: str, *, detail: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "archive_read_error",
        "error_type": code,
        "message": _safe_message(code),
    }
    if detail and code in {"invalid_archive_read", "limit_exceeded", "not_queryable"}:
        result["detail"] = detail[:200]
    return result


def _metadata_scope_value(metadata: Mapping[str, Any], name: str) -> Any:
    direct = metadata.get(name)
    if direct is not None:
        return direct
    nested = metadata.get("metadata")
    if isinstance(nested, Mapping):
        return nested.get(name)
    return None


def _public_metadata(item: ArtifactRef, metadata: Mapping[str, Any]) -> dict[str, Any]:
    result = item.to_dict()
    nested = metadata.get("metadata")
    if isinstance(nested, Mapping):
        safe_nested: dict[str, Any] = {}
        for key in (
            "call_id",
            "tool_name",
            "session_id",
            "run_id",
            "parent_run_id",
            "runtime_event_id",
            "rewrite_version",
            "lineage",
        ):
            value = nested.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                if value is not None:
                    safe_nested[key] = value
            elif isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
                safe_nested[key] = list(value)
        if safe_nested:
            result["metadata"] = safe_nested
    return result


class ToolResultArchiveCapability:
    """A scoped, read-only view over one Agent runtime archive.

    Capability derivation copies grants and never transfers ownership of the
    underlying archive or its metadata store.
    """

    def __init__(
        self,
        archive: ArtifactArchive,
        *,
        session_id: str,
        run_id: str | None = None,
        parent_run_id: str | None = None,
        allowed_run_ids: Iterable[str] | None = None,
        allowed_scopes: Iterable[str] = ARCHIVE_ALLOWED_SCOPES,
        default_limit: int = ARCHIVE_READ_DEFAULT_LIMIT,
        max_limit: int = ARCHIVE_READ_MAX_LIMIT,
    ) -> None:
        if not session_id:
            raise ValueError("session_id is required")
        if default_limit < 1 or max_limit < 1 or default_limit > max_limit:
            raise ValueError("invalid ArchiveRead limits")
        self.archive = archive
        self.session_id = str(session_id)
        self._run_id = str(run_id) if run_id else None
        self._parent_run_id = str(parent_run_id) if parent_run_id else None
        self.default_limit = int(default_limit)
        self.max_limit = int(max_limit)
        self.allowed_scopes = frozenset(str(item) for item in allowed_scopes)
        self._allowed_run_ids: set[str] = {
            str(item) for item in (allowed_run_ids or ()) if item
        }
        if run_id:
            self._allowed_run_ids.add(str(run_id))
        if parent_run_id:
            self._allowed_run_ids.add(str(parent_run_id))
        self._authorized_refs: set[str] = set()

    @property
    def tool_definition(self) -> dict[str, Any]:
        return {
            "name": ARCHIVE_READ_TOOL_NAME,
            "description": (
                "Read a bounded page or metadata from an archived tool result. "
                "Use the artifact ref supplied in a tool result; do not use file paths."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["inspect", "read", "query"],
                        "description": "Archive operation to perform.",
                    },
                    "ref": {
                        "type": "string",
                        "description": "The artifact reference supplied by a tool result.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Character or byte offset for read; defaults to 0.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": self.max_limit,
                        "description": "Bounded page size; defaults to the capability limit.",
                    },
                    "query": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": ARCHIVE_QUERY_MAX_FIELDS,
                        "description": "Allowed metadata fields for query.",
                    },
                    "expected_sha256": {
                        "type": "string",
                        "description": "Optional expected sha256 value.",
                    },
                    "expected_size_bytes": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Optional expected byte size.",
                    },
                },
                "required": ["operation", "ref"],
                "additionalProperties": False,
            },
        }

    def grant_run(self, run_id: str | None, parent_run_id: str | None = None) -> None:
        if run_id:
            self._allowed_run_ids.add(str(run_id))
            self._run_id = str(run_id)
        if parent_run_id:
            self._allowed_run_ids.add(str(parent_run_id))
            self._parent_run_id = str(parent_run_id)

    def register_ref(self, ref: ArtifactRef | Mapping[str, Any] | str) -> str:
        """Authorize a ref observed in the current canonical lineage.

        This is the compatibility bridge for old metadata that predates
        session/lineage fields. New archive writes include those fields and
        are still checked independently.
        """

        value = ref.ref if isinstance(ref, ArtifactRef) else (
            str(ref.get("ref")) if isinstance(ref, Mapping) else str(ref)
        )
        if not is_artifact_ref(value):
            raise ArchiveReadInputError("ref must use a supported artifact prefix")
        self._authorized_refs.add(value)
        return value

    def archive_result(
        self,
        value: Any,
        *,
        call_id: str | None = None,
        tool_name: str | None = None,
        runtime_event_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        """Persist a complete canonical result for a later projection.

        This is an internal projection operation.  The model-facing
        ``ArchiveRead`` tool remains read-only and page bounded.
        """

        validate_tool_result_size(value, tool_name or "tool-result")
        self._ensure_store_open()
        archive_value = value
        extra = dict(metadata or {})

        def set_scoped_metadata(name: str, scoped_value: Any) -> None:
            existing = extra.get(name)
            if existing is not None and str(existing) != str(scoped_value):
                raise ValueError(f"archive metadata {name} conflicts with capability scope")
            extra[name] = scoped_value

        set_scoped_metadata("session_id", self.session_id)
        if call_id is not None:
            set_scoped_metadata("call_id", str(call_id))
        if tool_name is not None:
            set_scoped_metadata("tool_name", str(tool_name))
        if runtime_event_id is not None:
            set_scoped_metadata("runtime_event_id", str(runtime_event_id))
        set_scoped_metadata("rewrite_version", ARCHIVE_REWRITE_VERSION)
        if self._run_id is not None:
            set_scoped_metadata("run_id", self._run_id)
        if self._parent_run_id is not None:
            set_scoped_metadata("parent_run_id", self._parent_run_id)
        if (
            isinstance(value, Mapping)
            and value.get("kind") == "binary_tool_result"
            and value.get("encoding") == "base64"
            and isinstance(value.get("data"), str)
        ):
            try:
                archive_value = base64.b64decode(value["data"], validate=True)
            except (ValueError, TypeError):
                raise ValueError("invalid binary tool result encoding")
            extra.setdefault("canonical_kind", "binary_tool_result")
        if isinstance(archive_value, str):
            mime_type = "text/plain"
            encoding = "utf-8"
        elif isinstance(archive_value, bytes):
            mime_type = "application/octet-stream"
            encoding = "binary"
        else:
            mime_type = "application/json"
            encoding = "binary"
        policy = RedactionPolicy(
            max_inline_bytes=MAX_TOOL_RESULT_BYTES,
            max_string_chars=MAX_TOOL_RESULT_BYTES,
        )
        logical_identity = {
            "session_id": self.session_id,
            "run_id": self._run_id or "",
            "parent_run_id": self._parent_run_id or "",
            "runtime_event_id": str(runtime_event_id or ""),
            "tool_call_id": str(call_id or ""),
            "tool_name": str(tool_name or "tool-result"),
            "rewrite_version": ARCHIVE_REWRITE_VERSION,
        }
        archived = self.archive.archive(
            archive_value,
            mime_type=mime_type,
            encoding=encoding,
            scope="tool-result",
            redaction_policy=policy,
            metadata=extra,
            logical_identity=logical_identity,
        )
        self.register_ref(archived.ref)
        return archived

    def derive(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        parent_run_id: str | None = None,
    ) -> "ToolResultArchiveCapability":
        """Derive a child capability without transferring archive ownership."""

        if session_id is not None and str(session_id) != self.session_id:
            raise ArchiveReadSessionError("derived capability must remain in the same session")
        child = ToolResultArchiveCapability(
            self.archive,
            session_id=self.session_id,
            run_id=run_id,
            parent_run_id=parent_run_id,
            allowed_run_ids=self._allowed_run_ids,
            allowed_scopes=self.allowed_scopes,
            default_limit=self.default_limit,
            max_limit=self.max_limit,
        )
        child._authorized_refs = set(self._authorized_refs)
        if run_id:
            self._allowed_run_ids.add(str(run_id))
        if parent_run_id:
            self._allowed_run_ids.add(str(parent_run_id))
        return child

    def _ensure_store_open(self) -> None:
        if _is_store_closed(getattr(self.archive, "metadata_store", None)):
            raise ArchiveStoreClosedError(_safe_message("archive_store_closed"))

    @staticmethod
    def _decode_full_payload(item: ArtifactRef, payload: bytes | str) -> Any:
        """Turn an archive payload into a provider-safe logical value.

        DurableToolBoundary stores structured values as canonical JSON bytes
        with ``encoding=binary``.  They are still JSON content, not opaque
        binary data, so hydrate those payloads back to a logical value before
        ``materialize_tool_result`` applies the provider-specific wire format.
        Other binary artifacts remain a bounded base64 string rather than
        leaking a bytes object across the provider boundary.
        """

        if isinstance(payload, str):
            return payload
        if item.mime_type == "application/json":
            text = payload.decode("utf-8", errors="replace")
            try:
                return json.loads(text)
            except (TypeError, ValueError):
                return text
        if item.encoding != "binary" or item.mime_type.startswith("text/"):
            return payload.decode(
                item.encoding if item.encoding != "binary" else "utf-8",
                errors="replace",
            )
        return "base64:" + base64.b64encode(payload).decode("ascii")

    def materialize(
        self,
        ref: ArtifactRef | Mapping[str, Any] | str,
        *,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
    ) -> Any:
        """Hydrate one complete redacted artifact for the first Provider use.

        This is intentionally an internal capability operation; the model
        facing ArchiveRead handler remains page-bounded and never exposes an
        implicit full-read operation.
        """

        item, _ = self._resolve_authorized(
            ref,
            expected_sha256=expected_sha256,
            expected_size_bytes=expected_size_bytes,
        )
        payload = self.archive.read(
            item,
            max_bytes=max(item.size_bytes, 1),
            preview=False,
            allowed_scopes=self.allowed_scopes,
        )
        return self._decode_full_payload(item, payload)

    def preview(
        self,
        ref: ArtifactRef | Mapping[str, Any] | str,
        *,
        offset: int = 0,
        limit: int = ARCHIVE_PREVIEW_CHARS,
    ) -> dict[str, Any]:
        """Return a bounded, human/model-readable prefix for capacity rescue."""

        return self.read(ref, offset=offset, limit=min(limit, ARCHIVE_PREVIEW_CHARS))

    def read_instructions(
        self,
        ref: ArtifactRef | Mapping[str, Any] | str,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> str:
        """Build a concrete, callable recovery hint for a placeholder."""

        value = ref.ref if isinstance(ref, ArtifactRef) else (
            str(ref.get("ref")) if isinstance(ref, Mapping) else str(ref)
        )
        requested_limit = self.default_limit if limit is None else int(limit)
        return (
            f'{ARCHIVE_READ_INSTRUCTIONS} Example: '
            f'{{"operation":"read","ref":{json.dumps(value, ensure_ascii=False)},'
            f'"offset":{max(int(offset), 0)},"limit":{requested_limit}}}'
        )

    def placeholder(
        self,
        ref: ArtifactRef | Mapping[str, Any] | str,
        *,
        preview: str | None = None,
        offset: int = 0,
        next_offset: int | None = None,
        truncated: bool = True,
        include_metadata: bool = True,
        total_units: int | None = None,
        unit: str | None = None,
        has_more: bool | None = None,
    ) -> dict[str, Any]:
        """Return a bounded, actionable provider-visible archive envelope."""

        item, _ = self._resolve_authorized(ref)
        result: dict[str, Any] = {
            "kind": "bounded_ref",
            "ref": item.ref,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
            "mime_type": item.mime_type,
            "encoding": item.encoding,
            "scope": item.scope,
            "redaction_version": item.redaction_version,
            "metadata_version": item.metadata_version,
            "truncated": bool(truncated),
            "read_instructions": self.read_instructions(
                item, offset=next_offset if next_offset is not None else offset
            ),
        }
        if include_metadata:
            metadata = self.archive.metadata(item)
            public = _public_metadata(item, metadata)
            nested = public.get("metadata")
            if isinstance(nested, Mapping) and nested:
                result["metadata"] = dict(nested)
        if preview is not None:
            result["preview"] = preview
            result["preview_chars"] = len(preview)
            result["offset"] = int(offset)
            result["next_offset"] = (
                int(next_offset) if next_offset is not None else int(offset) + len(preview)
            )
            if total_units is not None:
                result["total_units"] = max(0, int(total_units))
                result["omitted_units"] = max(
                    0, result["total_units"] - int(result["next_offset"])
                )
                if unit == "chars":
                    result["omitted_chars"] = result["omitted_units"]
            if unit is not None:
                result["unit"] = str(unit)
            if has_more is not None:
                result["has_more"] = bool(has_more)
        return result

    def _resolve_authorized(
        self,
        ref: ArtifactRef | Mapping[str, Any] | str,
        *,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
    ) -> tuple[ArtifactRef, dict[str, Any]]:
        self._ensure_store_open()
        item = self.archive.inspect(ref)
        if item.scope not in self.allowed_scopes:
            raise ArchiveReadScopeError(_safe_message("scope_denied"))
        metadata = self.archive.metadata(item)
        session_id = _metadata_scope_value(metadata, "session_id")
        if session_id is not None and str(session_id) != self.session_id:
            raise ArchiveReadSessionError(_safe_message("session_mismatch"))
        run_id = _metadata_scope_value(metadata, "run_id")
        parent_run_id = _metadata_scope_value(metadata, "parent_run_id")
        if run_id is not None or parent_run_id is not None:
            lineage = {str(value) for value in (run_id, parent_run_id) if value}
            if not lineage.intersection(self._allowed_run_ids):
                raise ArchiveReadScopeError(_safe_message("scope_denied"))
        elif item.ref not in self._authorized_refs:
            raise ArchiveReadSessionError(_safe_message("session_mismatch"))
        if expected_sha256 is not None and str(expected_sha256) != item.sha256:
            raise ArtifactIntegrityError("expected sha256 does not match artifact metadata")
        if expected_size_bytes is not None and int(expected_size_bytes) != item.size_bytes:
            raise ArtifactIntegrityError("expected size does not match artifact metadata")
        return item, metadata

    def inspect(self, ref: ArtifactRef | Mapping[str, Any] | str, **kwargs: Any) -> dict[str, Any]:
        item, metadata = self._resolve_authorized(ref, **kwargs)
        result = _public_metadata(item, metadata)
        result["kind"] = "archive_metadata"
        return result

    def read(
        self,
        ref: ArtifactRef | Mapping[str, Any] | str,
        *,
        offset: int = 0,
        limit: int | None = None,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
    ) -> dict[str, Any]:
        item, _ = self._resolve_authorized(
            ref,
            expected_sha256=expected_sha256,
            expected_size_bytes=expected_size_bytes,
        )
        requested = self.default_limit if limit is None else limit
        if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
            raise ArchiveReadRangeError("limit must be a positive integer")
        if requested > self.max_limit:
            raise ArchiveCapabilityError("ArchiveRead limit exceeds the bounded maximum")
        page: ArtifactPage = self.archive.read_page(
            item,
            offset=offset,
            limit=requested,
            allowed_scopes=self.allowed_scopes,
        )
        result = page.to_dict()
        if isinstance(result.get("page"), bytes):
            page_bytes = result["page"]
            if item.mime_type == "application/json":
                result["page"] = page_bytes.decode("utf-8", errors="replace")
                result["page_encoding"] = "utf-8"
            else:
                result["page"] = "base64:" + base64.b64encode(page_bytes).decode("ascii")
                result["page_encoding"] = "base64"
        else:
            result["page_encoding"] = item.encoding
        result["kind"] = "archive_page"
        return result

    def query(
        self,
        ref: ArtifactRef | Mapping[str, Any] | str,
        *,
        fields: Iterable[str] | None = None,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
    ) -> dict[str, Any]:
        item, metadata = self._resolve_authorized(
            ref,
            expected_sha256=expected_sha256,
            expected_size_bytes=expected_size_bytes,
        )
        allowed = {
            "ref",
            "sha256",
            "size_bytes",
            "mime_type",
            "encoding",
            "scope",
            "redaction_version",
            "created_at",
            "metadata_version",
            "artifact_id",
        }
        requested = list(fields) if fields is not None else sorted(allowed)
        if any(field not in allowed for field in requested):
            raise ArchiveCapabilityError("requested metadata field is not queryable")
        public = _public_metadata(item, metadata)
        return {
            "kind": "archive_query",
            "ref": item.ref,
            "fields": {field: public.get(field) for field in requested},
        }

    def execute(self, arguments: Mapping[str, Any] | Any) -> str:
        if not isinstance(arguments, Mapping):
            return json.dumps(_error_result("invalid_archive_read"), ensure_ascii=False)
        if "file_path" in arguments or "path" in arguments:
            return json.dumps(_error_result("invalid_archive_read"), ensure_ascii=False)
        operation = arguments.get("operation")
        ref = arguments.get("ref")
        if operation not in {"inspect", "read", "query"} or not isinstance(ref, str):
            return json.dumps(_error_result("invalid_archive_read"), ensure_ascii=False)
        try:
            common = {
                "expected_sha256": arguments.get("expected_sha256"),
                "expected_size_bytes": arguments.get("expected_size_bytes"),
            }
            if operation == "inspect":
                result = self.inspect(ref, **common)
            elif operation == "read":
                result = self.read(
                    ref,
                    offset=arguments.get("offset", 0),
                    limit=arguments.get("limit"),
                    **common,
                )
            else:
                fields = arguments.get("query")
                if fields is not None and not (
                    isinstance(fields, list)
                    and len(fields) <= ARCHIVE_QUERY_MAX_FIELDS
                    and all(isinstance(field, str) for field in fields)
                ):
                    raise ArchiveReadInputError("query must be a list of field names")
                result = self.query(ref, fields=fields, **common)
            return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        except ArchiveStoreClosedError:
            return json.dumps(_error_result("archive_store_closed"), ensure_ascii=False)
        except ArchiveReadSessionError:
            return json.dumps(_error_result("session_mismatch"), ensure_ascii=False)
        except ArchiveReadScopeError:
            return json.dumps(_error_result("scope_denied"), ensure_ascii=False)
        except ArtifactNotFoundError:
            return json.dumps(_error_result("not_found"), ensure_ascii=False)
        except ArtifactIntegrityError:
            return json.dumps(_error_result("integrity_mismatch"), ensure_ascii=False)
        except ArtifactSizeLimitError:
            return json.dumps(_error_result("invalid_range"), ensure_ascii=False)
        except ArtifactAccessError:
            return json.dumps(_error_result("scope_denied"), ensure_ascii=False)
        except ArchiveCapabilityError as error:
            code = getattr(error, "code", "capability_unavailable")
            if str(error).startswith("ArchiveRead limit"):
                code = "limit_exceeded"
            elif "queryable" in str(error):
                code = "not_queryable"
            return json.dumps(_error_result(code, detail=str(error)), ensure_ascii=False)
        except ArtifactArchiveError as error:
            code = {
                "artifact_not_found": "not_found",
                "artifact_metadata_error": "metadata_invalid",
                "artifact_integrity_error": "integrity_mismatch",
                "artifact_size_limit": "invalid_range",
            }.get(getattr(error, "code", ""), "artifact_archive_error")
            return json.dumps(_error_result(code), ensure_ascii=False)
        except (TypeError, ValueError, KeyError):
            return json.dumps(_error_result("invalid_archive_read"), ensure_ascii=False)


__all__ = [
    "ARCHIVE_ALLOWED_SCOPES",
    "ARCHIVE_PREVIEW_CHARS",
    "ARCHIVE_READ_DEFAULT_LIMIT",
    "ARCHIVE_READ_MAX_LIMIT",
    "ARCHIVE_READ_TOOL_NAME",
    "ARCHIVE_READ_INSTRUCTIONS",
    "ARCHIVE_QUERY_MAX_FIELDS",
    "ARCHIVE_REWRITE_VERSION",
    "ArchiveCapabilityError",
    "ArchiveReadInputError",
    "ArchiveReadRangeError",
    "ArchiveReadScopeError",
    "ArchiveReadSessionError",
    "ArchiveStoreClosedError",
    "ToolResultArchiveCapability",
]
