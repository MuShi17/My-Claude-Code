"""Deterministic privacy and bounded-payload helpers for event emission."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .runtime_event import FrozenDict, canonical_json_bytes, thaw

REDACTION_VERSION = "redaction-v1"
REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth|authorization|bearer|password|passwd|secret|private[_-]?key|client[_-]?secret|cookie|credential|api[_-]?token)(?:$|[_-])",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:sk-ant-[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]{12,}|bearer\s+[A-Za-z0-9._~+/=-]{12,}|AIza[A-Za-z0-9_-]{20,})",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """Versioned limits and patterns applied before a sink sees an event."""

    version: str = REDACTION_VERSION
    max_inline_bytes: int = 16_384
    max_string_chars: int = 8_192
    placeholder: str = REDACTED
    sensitive_keys: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("redaction policy version must not be empty")
        if self.max_inline_bytes < 1 or self.max_string_chars < 1:
            raise ValueError("redaction limits must be positive")


@dataclass(frozen=True, slots=True)
class RedactionReport:
    version: str
    redacted_paths: tuple[str, ...] = ()
    bounded_paths: tuple[str, ...] = ()

    @property
    def redacted_count(self) -> int:
        return len(self.redacted_paths)

    @property
    def bounded_count(self) -> int:
        return len(self.bounded_paths)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "redacted_count": self.redacted_count,
            "bounded_count": self.bounded_count,
        }


def _path(parent: str, key: object) -> str:
    return f"{parent}.{key}" if parent else str(key)


def _key_is_sensitive(key: object, policy: RedactionPolicy) -> bool:
    text = str(key)
    return bool(_SENSITIVE_KEY.search(text)) or any(
        marker.lower() in text.lower() for marker in policy.sensitive_keys
    )


def _string_is_sensitive(value: str) -> bool:
    return bool(_SENSITIVE_VALUE.search(value))


def redact_payload(
    value: Any,
    policy: RedactionPolicy | None = None,
    *,
    return_report: bool = False,
) -> Any:
    """Recursively scrub secrets while preserving event shape.

    The default return value is the redacted JSON-compatible payload.  Passing
    ``return_report=True`` returns ``(payload, RedactionReport)`` for the
    facade to attach policy metadata to the canonical event.
    """

    policy = policy or RedactionPolicy()
    redacted: list[str] = []
    bounded: list[str] = []

    def visit(item: Any, path: str, sensitive: bool = False) -> Any:
        if sensitive or (isinstance(item, str) and _string_is_sensitive(item)):
            redacted.append(path or "$")
            return policy.placeholder
        if isinstance(item, FrozenDict) or isinstance(item, dict):
            return {
                str(key): visit(child, _path(path, key), _key_is_sensitive(key, policy))
                for key, child in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [visit(child, _path(path, index)) for index, child in enumerate(item)]
        if isinstance(item, str) and len(item) > policy.max_string_chars:
            bounded.append(path or "$")
            return bounded_placeholder(item, ref=f"inline:{path or 'payload'}", policy=policy)
        return item

    result = visit(thaw(value), "")
    if return_report:
        return result, RedactionReport(
            version=policy.version,
            redacted_paths=tuple(redacted),
            bounded_paths=tuple(bounded),
        )
    return result


def redact_event_dict(
    event: dict[str, Any], policy: RedactionPolicy | None = None
) -> tuple[dict[str, Any], RedactionReport]:
    """Redact one event dict and add auditable policy metadata."""

    clean, report = redact_payload(event, policy, return_report=True)
    assert isinstance(clean, dict)
    metadata = dict(clean.get("metadata") or {})
    metadata["redaction_version"] = report.version
    if report.redacted_paths:
        metadata["redacted_paths"] = list(report.redacted_paths)
    if report.bounded_paths:
        metadata["bounded_paths"] = list(report.bounded_paths)
    clean["metadata"] = metadata
    return clean, report


def bounded_placeholder(
    value: Any,
    *,
    ref: str,
    mime_type: str = "application/json",
    policy: RedactionPolicy | None = None,
) -> dict[str, Any]:
    """Return metadata sufficient to retrieve a large value elsewhere."""

    policy = policy or RedactionPolicy()
    encoded = canonical_json_bytes(value)
    digest = hashlib.sha256(encoded).hexdigest()
    return {
        "kind": "bounded_ref",
        "ref": ref,
        "sha256": f"sha256:{digest}",
        "size_bytes": len(encoded),
        "mime_type": mime_type,
        "inline": encoded[: policy.max_inline_bytes].decode("utf-8", errors="replace")
        if len(encoded) <= policy.max_inline_bytes
        else None,
        "truncated": len(encoded) > policy.max_inline_bytes,
    }


def bound_payload(
    value: Any,
    *,
    ref: str,
    mime_type: str = "application/json",
    policy: RedactionPolicy | None = None,
) -> Any:
    """Keep small JSON payloads inline and reference large payloads."""

    policy = policy or RedactionPolicy()
    encoded = canonical_json_bytes(value)
    if len(encoded) <= policy.max_inline_bytes:
        return thaw(value)
    return bounded_placeholder(value, ref=ref, mime_type=mime_type, policy=policy)


def assert_redacted(value: Any) -> None:
    """Raise when common credential-shaped values remain in a payload."""

    serialised = json.dumps(thaw(value), ensure_ascii=False)
    if _SENSITIVE_VALUE.search(serialised):
        raise ValueError("payload still contains a credential-shaped value")


__all__ = [
    "REDACTED",
    "REDACTION_VERSION",
    "RedactionPolicy",
    "RedactionReport",
    "assert_redacted",
    "bound_payload",
    "bounded_placeholder",
    "redact_event_dict",
    "redact_payload",
]
