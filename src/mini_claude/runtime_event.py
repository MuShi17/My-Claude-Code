"""Provider-neutral canonical runtime event domain model.

This module deliberately contains no file, SQLite, provider SDK, or Agent
Loop code.  It is the small value-object boundary shared by the durable store
and all canonical projections.

The shape follows Maka's RuntimeEvent split between an immutable event
envelope and projections.  The input boundary is intentionally strict: only
the canonical envelope is accepted and legacy field inference is not allowed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, TypeAlias

from .event_ids import IdentityFactory, RunContext

SCHEMA_VERSION = 2
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
        required = {
            "schema_version", "id", "invocation_id", "run_id", "session_id",
            "turn_id", "ts", "partial", "role", "author",
        }
        optional = {
            "branch", "parent_run_id", "origin", "model_visibility", "status",
            "content", "actions", "refs", "metadata",
        }
        missing = sorted(key for key in required if key not in value)
        if missing:
            raise RuntimeEventValidationError(
                f"missing required fields: {', '.join(missing)}", field="envelope"
            )
        unknown = sorted(set(value) - required - optional)
        if unknown:
            raise RuntimeEventValidationError(
                f"legacy or unknown fields are not allowed: {', '.join(unknown)}",
                field="envelope",
            )
        return cls(**dict(value))

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
        # Terminality is a typed envelope fact.  ``end_run`` is retained as a
        # redundant action marker for recovery/display, but can never turn an
        # otherwise open event into a sealed run.
        return self.status in TERMINAL_STATUSES

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
        if self.actions and self.actions.get("end_run") is True and self.status not in TERMINAL_STATUSES:
            raise RuntimeEventValidationError(
                "end_run requires a terminal status", field="status"
            )
        if self.content is None and self.actions is None and self.refs is None:
            raise RuntimeEventValidationError("one of content/actions/refs is required", field="payload")
        if self.content is not None:
            if self.content.get("kind") == "invocation_opened" and self.partial:
                raise RuntimeEventValidationError(
                    "opening event cannot be partial", field="partial"
                )
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
        if kind == "invocation_opened":
            if content.get("protocol") != "invocation_opened_v1":
                raise RuntimeEventValidationError(
                    "opening requires protocol invocation_opened_v1", field="content.protocol"
                )
            for field in ("route", "configuration", "root", "source"):
                value = content.get(field)
                if not isinstance(value, Mapping) or not value:
                    raise RuntimeEventValidationError(
                        "opening requires a non-empty object", field=f"content.{field}"
                    )
            source_kind = content["source"].get("kind")
            if source_kind not in {"fresh", "continuation"}:
                raise RuntimeEventValidationError(
                    "source.kind must be fresh or continuation", field="content.source.kind"
                )
            root_kind = content["root"].get("kind")
            if root_kind not in {"user", "agent", "system"}:
                raise RuntimeEventValidationError(
                    "root.kind must identify the invocation root", field="content.root.kind"
                )


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
