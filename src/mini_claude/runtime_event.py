"""Provider-neutral canonical runtime event domain model.

This module deliberately contains no file, SQLite, provider SDK, or Agent
Loop code.  It is the small value-object boundary shared by the later durable
store and the legacy shadow adapter.

The shape follows Maka's RuntimeEvent split between an immutable event
envelope and projections, while retaining the Python project's frozen schema
version and the legacy fixture vocabulary (``kind``, ``call_id`` and ISO
``timestamp``) at the input boundary.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, TypeAlias

from .event_ids import IdentityFactory, RunContext, create_event_id

SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})
RUNTIME_EVENT_ROLES = ("user", "model", "tool", "system")
RUNTIME_EVENT_AUTHORS = ("user", "host", "agent", "tool", "system")
RUNTIME_EVENT_ORIGINS = ("provider", "code_mode")
RUNTIME_EVENT_MODEL_VISIBILITIES = ("visible", "hidden")
RUNTIME_EVENT_STATUSES = (
    "streaming",
    "completed",
    "failed",
    "aborted",
    "cancelled",
    "budget_exceeded",
)
TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "aborted", "cancelled", "budget_exceeded"}
)
CONTENT_KINDS = frozenset(
    {
        "text",
        "thinking",
        "function_call",
        "function_response",
        "error",
        "invocation_opened",
    }
)
ACTION_KINDS = frozenset({"permission", "tool_dispatch", "tool_outcome", "compaction"})


class FrozenDict(Mapping[str, Any]):
    """A recursively immutable mapping that still compares equal to a dict."""

    __slots__ = ("_data", "_hash")

    def __init__(self, value: Mapping[str, Any] | None = None) -> None:
        raw = dict(value or {})
        self._data = MappingProxyType({key: deep_freeze(item) for key, item in raw.items()})
        self._hash: int | None = None

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"FrozenDict({dict(self._data)!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return thaw(self) == thaw(other)
        return NotImplemented

    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash(canonical_json_bytes(thaw(self)))
        return self._hash


def deep_freeze(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict(value)
    if isinstance(value, list):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((deep_freeze(item) for item in value), key=repr))
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, FrozenDict) or isinstance(value, Mapping):
        return {str(key): thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Encode JSON with one deterministic representation for digest/replay."""

    return json.dumps(
        thaw(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class RuntimeEventError(ValueError):
    """Base class for errors at the canonical event boundary."""

    code = "runtime_event_error"


class RuntimeEventValidationError(RuntimeEventError):
    """The caller submitted an event that cannot be a canonical fact."""

    code = "invalid_runtime_event"

    def __init__(self, message: str, *, field: str | None = None) -> None:
        self.field = field
        prefix = f"{field}: " if field else ""
        super().__init__(prefix + message)


RuntimeEventContent: TypeAlias = FrozenDict
RuntimeEventActions: TypeAlias = FrozenDict
RuntimeEventRefs: TypeAlias = FrozenDict


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeEventValidationError("must be a non-empty string", field=field)
    return value


def _timestamp_ms(value: Any, field: str = "ts") -> int:
    if isinstance(value, bool):
        raise RuntimeEventValidationError("must be an integer epoch millisecond", field=field)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, datetime):
        instant = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(instant.astimezone(timezone.utc).timestamp() * 1000)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            instant = datetime.fromisoformat(text)
        except ValueError as error:
            raise RuntimeEventValidationError("must be ISO-8601 or epoch milliseconds", field=field) from error
        if instant.tzinfo is None:
            raise RuntimeEventValidationError("timestamp must include a timezone", field=field)
        return int(instant.astimezone(timezone.utc).timestamp() * 1000)
    raise RuntimeEventValidationError("must be an integer epoch millisecond", field=field)


def _legacy_kind(data: Mapping[str, Any]) -> str | None:
    kind = data.get("kind")
    if isinstance(kind, str):
        return kind
    content = data.get("content")
    if isinstance(content, Mapping) and isinstance(content.get("kind"), str):
        return str(content["kind"])
    actions = data.get("actions")
    if isinstance(actions, Mapping):
        for candidate in ACTION_KINDS:
            if candidate in actions:
                return candidate
    return None


def _defaults_for_kind(kind: str) -> tuple[str, str]:
    if kind in {"text", "thinking", "function_call"}:
        return "model", "agent"
    if kind in {"function_response", "tool_outcome"}:
        return "tool", "tool"
    if kind == "permission":
        return "system", "system"
    if kind in {"invocation_opened", "tool_dispatch", "compaction", "error"}:
        return "system", "system"
    return "system", "system"


def _normalise_legacy(data: Mapping[str, Any]) -> dict[str, Any]:
    """Accept C02 fixture/legacy vocabulary and produce the frozen envelope."""

    source = dict(data)
    kind = _legacy_kind(source)
    if kind is None:
        return source
    role, author = _defaults_for_kind(kind)
    def value_or(name: str, fallback: Any) -> Any:
        value = source.get(name)
        return fallback if value is None else value

    output: dict[str, Any] = {
        "schema_version": source.get("schema_version", SCHEMA_VERSION),
        "id": value_or("id", value_or("event_id", create_event_id())),
        "invocation_id": value_or("invocation_id", value_or("request_id", "legacy-invocation")),
        "run_id": value_or("run_id", "legacy-run"),
        "session_id": value_or("session_id", "legacy-session"),
        "turn_id": value_or("turn_id", "legacy-turn"),
        "ts": source.get("ts", source.get("timestamp")),
        "partial": source.get("partial", False),
        "role": source.get("role", role),
        "author": source.get("author", author),
    }
    for field in ("branch", "parent_run_id", "origin", "model_visibility", "status"):
        if field in source:
            output[field] = source[field]
    metadata: dict[str, Any] = dict(source.get("metadata") or {})
    if source.get("provider") is not None:
        metadata.setdefault("provider", source["provider"])
    # A canonical event already has its semantic kind in content/actions and
    # its lifecycle in metadata.  Add the compatibility marker only while
    # translating the old top-level ``kind`` vocabulary; otherwise a second
    # round-trip would incorrectly turn tool_outcome into function_response.
    if "kind" in source or "timestamp" in source:
        metadata.setdefault("legacy_kind", kind)
    if metadata:
        output["metadata"] = metadata

    if "content" in source and isinstance(source["content"], Mapping):
        output["content"] = dict(source["content"])
    elif kind == "text":
        output["content"] = {"kind": "text", "text": source.get("text", "")}
    elif kind == "thinking":
        output["content"] = {"kind": "thinking", "text": source.get("text", "")}
    elif kind == "function_call":
        output["content"] = {
            "kind": "function_call",
            "id": source.get("call_id") or source.get("tool_call_id") or "legacy-call",
            "name": source.get("name", "unknown"),
            "args": source.get("arguments", source.get("args", {})),
        }
    elif kind in {"function_response", "tool_outcome"}:
        output["content"] = {
            "kind": "function_response",
            "id": source.get("call_id") or source.get("tool_call_id") or "legacy-call",
            "name": source.get("name", "unknown"),
            "result": source.get("result", ""),
            "isError": not bool(source.get("success", True)) if kind == "tool_outcome" else bool(source.get("is_error", False)),
        }
    elif kind == "error":
        output["content"] = {
            "kind": "error",
            "message": source.get("message", "runtime error"),
            "code": source.get("error_type"),
        }
    elif kind == "invocation_opened":
        output["content"] = {"kind": "invocation_opened"}

    actions: dict[str, Any] = dict(source.get("actions") or {})
    if kind == "permission":
        actions.setdefault(
            "permission",
            {"decision": source.get("decision", "unknown"), "reason": source.get("reason", "")},
        )
    elif kind == "tool_dispatch":
        actions.setdefault(
            "tool_dispatch",
            {key: source[key] for key in ("name", "arguments_digest") if key in source},
        )
    elif kind == "tool_outcome":
        actions.setdefault(
            "tool_outcome",
            {key: source[key] for key in ("name", "success", "duration_ms", "error_type") if key in source},
        )
    elif kind == "compaction":
        actions.setdefault("compaction", {key: source[key] for key in source if key in {"source_high_water", "source_digest"}})
    if actions:
        output["actions"] = actions

    refs: dict[str, Any] = dict(source.get("refs") or {})
    call_id = source.get("call_id") or source.get("tool_call_id")
    if call_id is not None:
        refs.setdefault("tool_call_id", call_id)
    if refs:
        output["refs"] = refs
    return output


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """An immutable, provider-neutral runtime fact."""

    id: str
    invocation_id: str
    run_id: str
    session_id: str
    turn_id: str
    ts: int
    partial: bool
    role: str
    author: str
    schema_version: int = SCHEMA_VERSION
    branch: str | None = None
    parent_run_id: str | None = None
    origin: str | None = None
    model_visibility: str | None = None
    status: str | None = None
    content: RuntimeEventContent | None = None
    actions: RuntimeEventActions | None = None
    refs: RuntimeEventRefs | None = None
    metadata: FrozenDict | None = None

    def __post_init__(self) -> None:
        for field in ("id", "invocation_id", "run_id", "session_id", "turn_id"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        object.__setattr__(self, "ts", _timestamp_ms(self.ts))
        for name in ("content", "actions", "refs", "metadata"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, FrozenDict):
                if not isinstance(value, Mapping):
                    raise RuntimeEventValidationError("must be an object", field=name)
                object.__setattr__(self, name, FrozenDict(value))
        self.validate()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RuntimeEvent":
        if not isinstance(value, Mapping):
            raise RuntimeEventValidationError("event must be an object")
        data = _normalise_legacy(value)
        if data.get("ts") is None and data.get("timestamp") is None:
            raise RuntimeEventValidationError("timestamp is required", field="ts")
        kwargs = dict(data)
        if "timestamp" in kwargs and "ts" not in kwargs:
            kwargs["ts"] = kwargs.pop("timestamp")
        kwargs.pop("kind", None)
        # Legacy fixture names are translated into the canonical snake_case
        # envelope; unknown top-level presentation fields are retained as
        # metadata only when they are JSON-safe.
        known = {
            "schema_version", "id", "invocation_id", "run_id", "session_id", "turn_id", "ts",
            "timestamp", "partial", "role", "author", "branch", "parent_run_id", "origin",
            "model_visibility", "status", "content", "actions", "refs", "metadata",
        }
        extra = {key: value for key, value in kwargs.items() if key not in known}
        if extra:
            metadata = dict(kwargs.get("metadata") or {})
            metadata.setdefault("source_fields", extra)
            kwargs["metadata"] = metadata
        kwargs.pop("timestamp", None)
        return cls(**kwargs)

    @classmethod
    def create(
        cls,
        context: RunContext,
        *,
        role: str,
        author: str,
        partial: bool = False,
        ts: int | datetime | str | None = None,
        event_id: str | None = None,
        factory: IdentityFactory | None = None,
        **payload: Any,
    ) -> "RuntimeEvent":
        factory = factory or IdentityFactory()
        now = ts if ts is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
        return cls(
            id=event_id or factory.event_id(),
            invocation_id=context.invocation_id,
            run_id=context.run_id,
            session_id=context.session_id,
            turn_id=context.turn_id,
            ts=_timestamp_ms(now),
            partial=partial,
            role=role,
            author=author,
            branch=context.branch,
            parent_run_id=context.parent_run_id,
            **payload,
        )

    @property
    def timestamp(self) -> str:
        return datetime.fromtimestamp(self.ts / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @property
    def provider(self) -> str | None:
        return self.metadata.get("provider") if self.metadata else None

    @property
    def kind(self) -> str | None:
        if self.metadata and self.metadata.get("legacy_kind"):
            return str(self.metadata["legacy_kind"])
        if self.metadata and self.metadata.get("lifecycle") in {
            "invocation_opened",
            "usage",
            "permission",
            "tool_dispatch",
            "tool_outcome",
            "function_response",
        }:
            return str(self.metadata["lifecycle"])
        if self.content and self.content.get("kind"):
            return str(self.content["kind"])
        if self.actions:
            for kind in ACTION_KINDS:
                if kind in self.actions:
                    return kind
        return None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES or bool(self.actions and self.actions.get("end_run") is True)

    @property
    def is_partial(self) -> bool:
        return self.partial

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "id": self.id,
            "invocation_id": self.invocation_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "ts": self.ts,
            "partial": self.partial,
            "role": self.role,
            "author": self.author,
        }
        for name in ("branch", "parent_run_id", "origin", "model_visibility", "status"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        for name in ("content", "actions", "refs", "metadata"):
            value = getattr(self, name)
            if value is not None:
                result[name] = thaw(value)
        return result

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @property
    def canonical_digest(self) -> str:
        return self.digest()

    def validate(self) -> None:
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise RuntimeEventValidationError(
                f"unsupported schema version {self.schema_version!r}", field="schema_version"
            )
        if not isinstance(self.ts, int) or isinstance(self.ts, bool):
            raise RuntimeEventValidationError("must be an integer epoch millisecond", field="ts")
        if not isinstance(self.partial, bool):
            raise RuntimeEventValidationError("must be boolean", field="partial")
        if self.role not in RUNTIME_EVENT_ROLES:
            raise RuntimeEventValidationError(f"unsupported role {self.role!r}", field="role")
        if self.author not in RUNTIME_EVENT_AUTHORS:
            raise RuntimeEventValidationError(f"unsupported author {self.author!r}", field="author")
        for field, allowed in (
            ("origin", RUNTIME_EVENT_ORIGINS),
            ("model_visibility", RUNTIME_EVENT_MODEL_VISIBILITIES),
            ("status", RUNTIME_EVENT_STATUSES),
        ):
            value = getattr(self, field)
            if value is not None and value not in allowed:
                raise RuntimeEventValidationError(f"unsupported value {value!r}", field=field)
        if self.parent_run_id is not None:
            _identifier(self.parent_run_id, "parent_run_id")
            if self.parent_run_id == self.run_id:
                raise RuntimeEventValidationError("cannot equal run_id", field="parent_run_id")
        if self.branch is not None:
            _identifier(self.branch, "branch")
        if self.status in TERMINAL_STATUSES and self.partial:
            raise RuntimeEventValidationError("terminal event cannot be partial", field="partial")
        if self.content is None and self.actions is None and self.refs is None:
            raise RuntimeEventValidationError("one of content/actions/refs is required", field="payload")
        if self.content is not None:
            self._validate_content(self.content)
        if self.actions is not None:
            if not self.actions:
                raise RuntimeEventValidationError("must not be empty", field="actions")
            for key in self.actions:
                if not isinstance(key, str) or not key.strip():
                    raise RuntimeEventValidationError("action keys must be strings", field="actions")
        if self.refs is not None:
            for key, value in self.refs.items():
                if not isinstance(key, str) or not isinstance(value, (str, int, float, bool, type(None))):
                    raise RuntimeEventValidationError("refs must contain scalar values", field=f"refs.{key}")
        try:
            canonical_json_bytes(self.to_dict())
        except (TypeError, ValueError) as error:
            raise RuntimeEventValidationError(f"payload is not JSON-safe: {error}", field="payload") from error

    @staticmethod
    def _validate_content(content: Mapping[str, Any]) -> None:
        kind = content.get("kind")
        if kind not in CONTENT_KINDS:
            raise RuntimeEventValidationError(f"unsupported content kind {kind!r}", field="content.kind")
        if kind in {"text", "thinking"} and not isinstance(content.get("text"), str):
            raise RuntimeEventValidationError("text content requires a string", field="content.text")
        if kind == "function_call":
            for field in ("id", "name"):
                _identifier(content.get(field), f"content.{field}")
            if "args" not in content:
                raise RuntimeEventValidationError("function call requires args", field="content.args")
        if kind == "function_response":
            for field in ("id", "name"):
                _identifier(content.get(field), f"content.{field}")
            if "result" not in content:
                raise RuntimeEventValidationError("function response requires result", field="content.result")
        if kind == "error" and not isinstance(content.get("message"), str):
            raise RuntimeEventValidationError("error content requires message", field="content.message")


def is_terminal_runtime_event(event: RuntimeEvent) -> bool:
    return event.is_terminal


def is_partial_runtime_event(event: RuntimeEvent) -> bool:
    return event.partial


def runtime_event_has_model_visible_content(event: RuntimeEvent) -> bool:
    if event.model_visibility == "hidden" or event.partial or event.content is None:
        return False
    return event.content.get("kind") in {"text", "thinking", "function_call", "function_response"}


__all__ = [
    "ACTION_KINDS",
    "CONTENT_KINDS",
    "FrozenDict",
    "RunContext",
    "RuntimeEvent",
    "RuntimeEventActions",
    "RuntimeEventContent",
    "RuntimeEventError",
    "RuntimeEventRefs",
    "RuntimeEventValidationError",
    "SCHEMA_VERSION",
    "canonical_json_bytes",
    "is_partial_runtime_event",
    "is_terminal_runtime_event",
    "runtime_event_has_model_visible_content",
]
