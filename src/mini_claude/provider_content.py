"""Deterministic, provider-safe materialization of tool results."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .runtime_event import canonical_json_bytes


def provider_value_type(value: Any) -> str:
    """Return a payload-free type label suitable for diagnostics."""

    if value is None:
        return "null"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "mapping"
    if isinstance(value, (list, tuple)):
        return "sequence"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return type(value).__name__
    return "object"


class ProviderToolContentError(ValueError):
    """A tool result cannot be represented as safe provider content."""

    code = "provider_tool_content_normalization_failed"

    def __init__(self, value: Any, *, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        self.value_type = provider_value_type(value)
        super().__init__(
            "tool result content rejected: "
            f"provider={provider} value_type={self.value_type} reason={reason}"
        )


def _canonical_value(value: Any, *, provider: str) -> Any:
    try:
        return json.loads(canonical_json_bytes(value))
    except (TypeError, ValueError, OverflowError) as error:
        raise ProviderToolContentError(
            value, provider=provider, reason="not_json_serializable"
        ) from error


def _valid_image_source(source: Any) -> bool:
    if not isinstance(source, Mapping) or not isinstance(source.get("type"), str):
        return False
    source_type = source["type"]
    if source_type == "base64":
        return all(
            isinstance(source.get(key), str)
            for key in ("media_type", "data")
        )
    if source_type == "url":
        return isinstance(source.get("url"), str)
    return False


def _valid_content_block(block: Any) -> bool:
    if not isinstance(block, Mapping) or not isinstance(block.get("type"), str):
        return False
    block_type = block["type"]
    if block_type == "text":
        return isinstance(block.get("text"), str)
    if block_type == "image":
        return _valid_image_source(block.get("source"))
    if block_type == "document":
        return _valid_image_source(block.get("source")) or isinstance(
            block.get("source"), Mapping
        )
    return False


def is_valid_content_blocks(value: Any) -> bool:
    """Return whether a non-empty list is an explicitly supported block list."""

    return isinstance(value, list) and bool(value) and all(
        _valid_content_block(item) for item in value
    )


def materialize_tool_result(value: Any, *, provider: str) -> str | list[dict[str, Any]]:
    """Build the one deterministic representation visible to a Provider.

    Strings remain strings.  Anthropic may receive an explicitly validated
    content-block sequence.  Every other JSON value is compact, recursively
    key-sorted JSON text; OpenAI-compatible providers always receive text for
    structured values.
    """

    if provider not in {"anthropic", "openai"}:
        raise ValueError(f"unsupported provider {provider!r}")
    if isinstance(value, str):
        return value

    normalized = _canonical_value(value, provider=provider)
    if provider == "anthropic" and is_valid_content_blocks(normalized):
        return normalized
    try:
        return canonical_json_bytes(normalized).decode("utf-8")
    except (TypeError, ValueError, OverflowError) as error:
        raise ProviderToolContentError(
            value, provider=provider, reason="canonical_encoding_failed"
        ) from error


def materialized_content_bytes(value: str | list[dict[str, Any]]) -> bytes:
    """Return the exact bytes represented by an already materialized value."""

    if isinstance(value, str):
        return value.encode("utf-8")
    return canonical_json_bytes(value)


def display_tool_result(value: str | list[dict[str, Any]]) -> str:
    """Render a materialized result for the terminal without changing it."""

    if isinstance(value, str):
        return value
    return canonical_json_bytes(value).decode("utf-8")


__all__ = [
    "ProviderToolContentError",
    "display_tool_result",
    "is_valid_content_blocks",
    "materialize_tool_result",
    "materialized_content_bytes",
    "provider_value_type",
]
