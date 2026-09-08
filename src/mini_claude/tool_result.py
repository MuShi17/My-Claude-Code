"""Common public safety boundary for tool results.

The canonical runtime keeps the complete safe result.  This module only
enforces the public ingress limit and builds a bounded, payload-free error
when a tool exceeds it.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

from .runtime_event import canonical_json_bytes

# Public tool-result safety cap: 16 MiB measured as canonical JSON UTF-8 bytes.
MAX_TOOL_RESULT_BYTES = 16 * 1024 * 1024
# Kept as an import-compatible alias for callers that used the old symbol.
# The boundary is no longer measured in Unicode characters.
MAX_TOOL_RESULT_CHARS = MAX_TOOL_RESULT_BYTES
TOOL_RESULT_ERROR_KIND = "tool_result_error"


def _measurement_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda item: f"<{type(item).__name__}>",
        )
    except (TypeError, ValueError, OverflowError):
        return str(value)


def tool_result_char_count(value: Any) -> int:
    """Count the public result in Unicode characters."""

    return len(_measurement_text(value))


def canonical_tool_result(value: Any) -> Any:
    """Convert the only non-JSON-native tool value to a safe canonical value."""

    if isinstance(value, bytes):
        return {
            "kind": "binary_tool_result",
            "encoding": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }
    return value


class ToolResultSerializationError(ValueError):
    """Raised when a tool result cannot cross the canonical JSON boundary."""

    code = "result_not_serializable"

    def __init__(self, tool_name: str, error: BaseException | None = None) -> None:
        self.tool_name = str(tool_name)
        self.detail = type(error).__name__ if error is not None else "serialization_error"
        super().__init__(self.code)

    def payload(self) -> dict[str, Any]:
        return {
            "kind": TOOL_RESULT_ERROR_KIND,
            "error_type": self.code,
            "tool_name": self.tool_name,
            "message": "tool result cannot be represented as canonical JSON",
        }


def canonical_tool_result_bytes(value: Any, *, tool_name: str = "tool-result") -> bytes:
    """Return the exact canonical JSON UTF-8 representation used for sizing.

    The conversion happens before measurement so binary results include their
    Base64 envelope and JSON strings include their quotes/escaping, matching
    the serialized-byte boundary used by Maka.
    """

    normalized = canonical_tool_result(value)
    try:
        return canonical_json_bytes(normalized)
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ToolResultSerializationError(tool_name, error) from error


def tool_result_byte_count(value: Any, *, tool_name: str = "tool-result") -> int:
    """Count a normalized tool result in canonical JSON UTF-8 bytes."""

    return len(canonical_tool_result_bytes(value, tool_name=tool_name))


def result_too_large_payload(
    tool_name: str,
    actual_bytes: int,
    *,
    limit_bytes: int = MAX_TOOL_RESULT_BYTES,
) -> dict[str, Any]:
    """Return a stable error that contains no result payload or traceback."""

    payload: dict[str, Any] = {
        "kind": TOOL_RESULT_ERROR_KIND,
        "error_type": "result_too_large",
        "tool_name": str(tool_name),
        "limit_bytes": int(limit_bytes),
        "actual_bytes": int(actual_bytes),
        "message": (
            f"tool result exceeds the common {int(limit_bytes)}-byte limit"
        ),
    }
    if str(tool_name) == "read_file":
        payload["hint"] = (
            "Retry read_file with a non-negative 0-based offset and a positive "
            "line limit, for example offset=0, limit=6000."
        )
    else:
        payload["hint"] = "Retry the tool with a smaller result or narrower query."
    return payload


class ToolResultLimitError(ValueError):
    """Raised at a durable boundary when a result exceeds the public limit."""

    code = "result_too_large"

    def __init__(self, tool_name: str, actual_bytes: int) -> None:
        self.tool_name = str(tool_name)
        self.actual_bytes = int(actual_bytes)
        super().__init__(self.code)

    def payload(self) -> dict[str, Any]:
        return result_too_large_payload(self.tool_name, self.actual_bytes)


def validate_tool_result_size(
    value: Any,
    tool_name: str,
    *,
    limit_bytes: int = MAX_TOOL_RESULT_BYTES,
) -> int:
    """Validate one result and return its normalized JSON UTF-8 byte size."""

    actual_bytes = tool_result_byte_count(value, tool_name=tool_name)
    if actual_bytes > limit_bytes:
        raise ToolResultLimitError(tool_name, actual_bytes)
    return actual_bytes


def public_tool_result(value: Any, tool_name: str) -> str:
    """Convert a tool return value to safe public text under the common gate."""

    try:
        normalized = canonical_tool_result(value)
        serialized = canonical_tool_result_bytes(normalized, tool_name=tool_name)
        if len(serialized) > MAX_TOOL_RESULT_BYTES:
            raise ToolResultLimitError(tool_name, len(serialized))
        value = normalized
    except ToolResultLimitError as error:
        value = error.payload()
    except ToolResultSerializationError as error:
        value = error.payload()
    if isinstance(value, str):
        return value
    try:
        return canonical_json_bytes(value).decode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return json.dumps(
            {"kind": "tool_result_error", "error_type": "result_not_serializable"},
            ensure_ascii=False,
            separators=(",", ":"),
        )


def parsed_result_error(value: Any) -> Mapping[str, Any] | None:
    """Parse a public structured tool error without interpreting arbitrary text."""

    candidate: Any = value
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except (TypeError, ValueError):
            return None
    if not isinstance(candidate, Mapping):
        return None
    if candidate.get("kind") != TOOL_RESULT_ERROR_KIND:
        return None
    return candidate


def is_tool_result_error(value: Any) -> bool:
    return parsed_result_error(value) is not None


__all__ = [
    "MAX_TOOL_RESULT_BYTES",
    "MAX_TOOL_RESULT_CHARS",
    "TOOL_RESULT_ERROR_KIND",
    "ToolResultLimitError",
    "ToolResultSerializationError",
    "canonical_tool_result",
    "canonical_tool_result_bytes",
    "is_tool_result_error",
    "parsed_result_error",
    "public_tool_result",
    "result_too_large_payload",
    "tool_result_byte_count",
    "tool_result_char_count",
    "validate_tool_result_size",
]
