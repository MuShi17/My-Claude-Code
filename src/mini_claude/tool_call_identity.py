"""Shared identity rules for canonical final tool-call events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .runtime_event import RuntimeEvent, canonical_json_bytes


def decode_tool_arguments(raw: Mapping[str, Any] | str | Any) -> tuple[Any, str | None]:
    """Decode provider arguments without silently converting invalid JSON to ``{}``."""

    if isinstance(raw, Mapping):
        return dict(raw), None
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            return raw, f"invalid_json:{error.msg}"
        if not isinstance(value, Mapping):
            return value, "tool arguments must decode to an object"
        return dict(value), None
    return raw, "tool arguments must be an object or JSON object string"


@dataclass(frozen=True, slots=True)
class ToolCallSignature:
    """Stable payload identity used by writers and derived projections."""

    name: str
    arguments: bytes
    decode_error: str | None = None


def tool_call_signature(name: Any, arguments: Any) -> ToolCallSignature:
    """Normalize a tool name and arguments into a comparable safe signature."""

    decoded, error = decode_tool_arguments(arguments)
    try:
        encoded = canonical_json_bytes(decoded)
    except (TypeError, ValueError):
        encoded = canonical_json_bytes({"unserializable": str(decoded)})
    return ToolCallSignature(str(name), encoded, error)


def is_final_tool_call(event: RuntimeEvent) -> bool:
    """Return whether an event is the canonical final-call fact."""

    content = event.content or {}
    metadata = event.metadata or {}
    return (
        not event.partial
        and content.get("kind") == "function_call"
        and metadata.get("lifecycle") == "tool_call_final"
    )


def event_tool_call_signature(event: RuntimeEvent) -> ToolCallSignature:
    """Build a signature from a validated canonical function-call event."""

    content = event.content or {}
    return tool_call_signature(content.get("name", ""), content.get("args"))


def equivalent_tool_call(first: RuntimeEvent, second: RuntimeEvent) -> bool:
    """Compare only the payload portion of two final call facts."""

    return event_tool_call_signature(first) == event_tool_call_signature(second)


__all__ = [
    "ToolCallSignature",
    "decode_tool_arguments",
    "equivalent_tool_call",
    "event_tool_call_signature",
    "is_final_tool_call",
    "tool_call_signature",
]
