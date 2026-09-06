"""Canonical RuntimeEvent sink contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, Protocol, runtime_checkable

from .context_transition import (
    ContextTransition,
    ContextTransitionError,
    replacement_digest,
)
from .redaction import RedactionPolicy, redact_event_dict
from .projections.base import stable_digest
from .runtime_event import RuntimeEvent, RuntimeEventError, RuntimeEventValidationError


class EventSinkError(RuntimeError):
    """Base class for transport or lifecycle failures in an EventSink."""


class CanonicalSinkError(EventSinkError):
    """Canonical persistence failed; the caller must not claim durability."""


class SinkClosedError(EventSinkError):
    """A sink was used after close."""


def _delegate_optional(sink: Any, name: str, *args: Any) -> Any:
    method = getattr(sink, name, None)
    return method(*args) if callable(method) else None


@runtime_checkable
class EventSink(Protocol):
    """The smallest protocol shared by recording and SQLite sinks."""

    def emit(self, event: RuntimeEvent) -> RuntimeEvent:
        ...

    def flush(self) -> None:
        ...

    def close(self) -> None:
        ...


def _as_event(event: RuntimeEvent | dict[str, Any]) -> RuntimeEvent:
    if isinstance(event, RuntimeEvent):
        event.validate()
        return event
    try:
        return RuntimeEvent.from_dict(event)
    except RuntimeEventError:
        raise
    except Exception as error:
        raise RuntimeEventValidationError(str(error)) from error


def _prepare_event(
    event: RuntimeEvent | dict[str, Any], policy: RedactionPolicy
) -> RuntimeEvent:
    """Redact an event and finalize digest metadata for effective context."""

    source = _as_event(event)
    source_dict = source.to_dict()
    raw_actions = source_dict.get("actions") or {}
    raw_transition = raw_actions.get("context_transition") if isinstance(raw_actions, Mapping) else None
    if isinstance(raw_transition, Mapping):
        try:
            # Validate the producer-supplied transition before redaction. This
            # prevents the preparation boundary from accepting a tampered
            # replacement digest and then silently repairing it.
            parsed_transition = ContextTransition.from_value(raw_transition)
            raw_compaction = raw_actions.get("compaction") if isinstance(raw_actions, Mapping) else None
            expected_result = None
            if parsed_transition.replacements:
                expected_result = stable_digest(
                    {"replacements": [item.to_dict() for item in parsed_transition.replacements]}
                )
            elif isinstance(raw_compaction, Mapping) and raw_compaction.get("reset_model_context"):
                expected_result = stable_digest(raw_compaction.get("context_messages", []))
            if expected_result is not None and parsed_transition.result_digest != expected_result:
                raise ContextTransitionError("transition result digest mismatch")
        except ContextTransitionError as error:
            raise RuntimeEventValidationError(
                str(error), field="actions.context_transition"
            ) from error

    clean, _ = redact_event_dict(source_dict, policy)
    actions = clean.get("actions") or {}
    transition_value = actions.get("context_transition") if isinstance(actions, Mapping) else None
    if isinstance(transition_value, Mapping):
        transition = dict(transition_value)
        clean_replacements: list[dict[str, Any]] = []
        for item in transition.get("replacements", []):
            replacement = dict(item)
            if "replacement" in replacement:
                replacement["replacement_digest"] = replacement_digest(
                    replacement["replacement"]
                )
            clean_replacements.append(replacement)
        transition["replacements"] = clean_replacements
        compaction = actions.get("compaction") if isinstance(actions, Mapping) else None
        if clean_replacements:
            transition["result_digest"] = stable_digest(
                {"replacements": clean_replacements}
            )
        elif isinstance(compaction, Mapping) and compaction.get("reset_model_context"):
            transition["result_digest"] = stable_digest(
                compaction.get("context_messages", [])
            )
        actions = dict(actions)
        actions["context_transition"] = transition
        clean["actions"] = actions
        try:
            ContextTransition.from_value(transition)
        except ContextTransitionError as error:
            raise RuntimeEventValidationError(
                str(error), field="actions.context_transition"
            ) from error
    return RuntimeEvent.from_dict(clean)


class RecordingEventSink:
    """In-memory canonical sink used by offline contracts and dry runs."""

    canonical = True

    def __init__(
        self,
        *,
        failure_hook: Callable[[str, RuntimeEvent], None] | None = None,
    ) -> None:
        self.events: list[RuntimeEvent] = []
        self._by_id: dict[str, RuntimeEvent] = {}
        self._failure_hook = failure_hook
        self._closed = False

    def emit(self, event: RuntimeEvent) -> RuntimeEvent:
        if self._closed:
            raise SinkClosedError("recording sink is closed")
        event = _as_event(event)
        if self._failure_hook:
            try:
                self._failure_hook("emit", event)
            except Exception as error:
                raise CanonicalSinkError(str(error)) from error
        previous = self._by_id.get(event.id)
        if previous is not None:
            if previous.digest() != event.digest():
                raise CanonicalSinkError(f"event id conflict: {event.id}")
            return previous
        self._by_id[event.id] = event
        self.events.append(event)
        return event

    append = emit

    def flush(self) -> None:
        if self._closed:
            return
        if self._failure_hook and self.events:
            try:
                self._failure_hook("flush", self.events[-1])
            except Exception as error:
                raise CanonicalSinkError(str(error)) from error

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        self._closed = True


MemoryEventSink = RecordingEventSink


class CanonicalSink:
    """Validate/redact once, then delegate to a canonical EventSink."""

    canonical = True

    def __init__(
        self,
        downstream: EventSink | None = None,
        *,
        redaction_policy: RedactionPolicy | None = None,
    ) -> None:
        self.downstream = downstream or RecordingEventSink()
        self.redaction_policy = redaction_policy or RedactionPolicy()

    def prepare(self, event: RuntimeEvent | dict[str, Any]) -> RuntimeEvent:
        return _prepare_event(event, self.redaction_policy)

    def emit(self, event: RuntimeEvent | dict[str, Any]) -> RuntimeEvent:
        clean = self.prepare(event)
        try:
            result = self.downstream.emit(clean)
        except CanonicalSinkError:
            raise
        except Exception as error:
            raise CanonicalSinkError(str(error)) from error
        return result if isinstance(result, RuntimeEvent) else clean

    append = emit

    def flush(self) -> None:
        try:
            self.downstream.flush()
        except CanonicalSinkError:
            raise
        except Exception as error:
            raise CanonicalSinkError(str(error)) from error

    def close(self) -> None:
        try:
            self.downstream.close()
        except CanonicalSinkError:
            raise
        except Exception as error:
            raise CanonicalSinkError(str(error)) from error

    def read_tool_operation(self, operation_id: str) -> Any:
        return _delegate_optional(self.downstream, "read_tool_operation", operation_id)

    def read_tool_operation_for_call(self, invocation_id: str, provider_tool_call_id: str) -> Any:
        return _delegate_optional(
            self.downstream,
            "read_tool_operation_for_call",
            invocation_id,
            provider_tool_call_id,
        )


CanonicalEventSink = CanonicalSink


class RuntimeEventEmitter:
    """Facade accepting dicts or RuntimeEvent values at one emission boundary."""

    def __init__(
        self,
        sink: EventSink,
        *,
        redaction_policy: RedactionPolicy | None = None,
    ) -> None:
        self.sink = sink
        self.redaction_policy = redaction_policy or RedactionPolicy()

    def emit(self, event: RuntimeEvent | dict[str, Any]) -> RuntimeEvent:
        return self.sink.emit(self.prepare(event))

    append = emit

    def prepare(self, event: RuntimeEvent | dict[str, Any]) -> RuntimeEvent:
        return _prepare_event(event, self.redaction_policy)

    def flush(self) -> None:
        self.sink.flush()

    def close(self) -> None:
        self.sink.close()

    def read_tool_operation(self, operation_id: str) -> Any:
        return _delegate_optional(self.sink, "read_tool_operation", operation_id)

    def read_tool_operation_for_call(self, invocation_id: str, provider_tool_call_id: str) -> Any:
        return _delegate_optional(
            self.sink,
            "read_tool_operation_for_call",
            invocation_id,
            provider_tool_call_id,
        )


EventEmitter = RuntimeEventEmitter


__all__ = [
    "CanonicalSink",
    "CanonicalEventSink",
    "CanonicalSinkError",
    "EventEmitter",
    "EventSink",
    "EventSinkError",
    "MemoryEventSink",
    "RecordingEventSink",
    "RuntimeEventEmitter",
    "SinkClosedError",
]
