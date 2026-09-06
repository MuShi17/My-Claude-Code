"""Canonical RuntimeEvent sink contracts."""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from .redaction import RedactionPolicy, redact_event_dict
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
        source = _as_event(event)
        clean, _ = redact_event_dict(source.to_dict(), self.redaction_policy)
        return RuntimeEvent.from_dict(clean)

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
        source = _as_event(event)
        clean, _ = redact_event_dict(source.to_dict(), self.redaction_policy)
        return self.sink.emit(RuntimeEvent.from_dict(clean))

    append = emit

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
