"""Canonical/legacy event sinks and their explicit shadow-write policy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from .redaction import RedactionPolicy, redact_event_dict
from .runtime_event import RuntimeEvent, RuntimeEventError, RuntimeEventValidationError, thaw


class EventSinkError(RuntimeError):
    """Base class for transport or lifecycle failures in an EventSink."""


class CanonicalSinkError(EventSinkError):
    """Canonical persistence failed; the caller must not claim durability."""


class DiagnosticSinkError(EventSinkError):
    """A legacy/diagnostic projection failed after canonical emission."""


class SinkClosedError(EventSinkError):
    """A sink was used after close."""


@dataclass(frozen=True, slots=True)
class DiagnosticFailure:
    sink: str
    event_id: str
    error_type: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "sink": self.sink,
            "event_id": self.event_id,
            "error_type": self.error_type,
            "message": self.message,
        }


@runtime_checkable
class EventSink(Protocol):
    """The smallest protocol shared by recording, SQLite and shadow sinks."""

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


CanonicalEventSink = CanonicalSink


def legacy_mapping(event: RuntimeEvent) -> dict[str, Any]:
    """Make a readable legacy JSONL record without inventing side effects."""

    kind = event.kind or "runtime"
    record: dict[str, Any] = {
        "type": "runtime_event",
        "runtime_kind": kind,
        "canonical_event_id": event.id,
        "session_id": event.session_id,
        "turn_id": event.turn_id,
        "run_id": event.run_id,
        "invocation_id": event.invocation_id,
        "timestamp": event.timestamp,
        "partial": event.partial,
        "agent_id": (event.metadata or {}).get("agent_id", "main"),
    }
    if event.parent_run_id:
        record["parent_run_id"] = event.parent_run_id
    if event.provider:
        record["provider"] = event.provider
    if event.content:
        record["content"] = thaw(event.content)
    if event.actions:
        record["actions"] = thaw(event.actions)
    if event.refs:
        record["refs"] = thaw(event.refs)
    if event.metadata:
        record["runtime_metadata"] = thaw(event.metadata)
    if event.status:
        record["status"] = event.status
    return record


class LegacyShadowSink:
    """Diagnostic adapter to AgentLogger/SessionTracer-compatible JSONL."""

    canonical = False

    def __init__(
        self,
        logger: Any,
        *,
        tracer: Any | None = None,
        strict: bool = False,
        redaction_policy: RedactionPolicy | None = None,
        sink_name: str = "legacy",
    ) -> None:
        self.logger = logger
        self.tracer = tracer
        self.strict = strict
        self.redaction_policy = redaction_policy or RedactionPolicy()
        self.sink_name = sink_name
        self.failures: list[DiagnosticFailure] = []
        self._closed = False

    def emit(self, event: RuntimeEvent | dict[str, Any]) -> RuntimeEvent:
        if self._closed:
            raise SinkClosedError("legacy shadow sink is closed")
        source = _as_event(event)
        clean, _ = redact_event_dict(source.to_dict(), self.redaction_policy)
        clean_event = RuntimeEvent.from_dict(clean)
        record = legacy_mapping(clean_event)
        try:
            if hasattr(self.logger, "log_runtime_event"):
                self.logger.log_runtime_event(record)
            elif hasattr(self.logger, "_write_event"):
                # Compatibility fallback for pre-C03 logger implementations.
                self.logger._write_event(record)
            elif hasattr(self.logger, "write"):
                self.logger.write(json.dumps(record, ensure_ascii=False) + "\n")
                if hasattr(self.logger, "flush"):
                    self.logger.flush()
            else:
                raise TypeError("legacy logger has no compatible event writer")
        except Exception as error:
            failure = DiagnosticFailure(
                sink=self.sink_name,
                event_id=clean_event.id,
                error_type=type(error).__name__,
                message=str(error),
            )
            self.failures.append(failure)
            if self.strict:
                raise DiagnosticSinkError(str(error)) from error
        return clean_event

    append = emit

    def flush(self) -> None:
        if self._closed:
            return
        try:
            if hasattr(self.logger, "flush"):
                self.logger.flush()
            if self.tracer is not None and hasattr(self.tracer, "flush"):
                self.tracer.flush()
        except Exception as error:
            failure = DiagnosticFailure(self.sink_name, "flush", type(error).__name__, str(error))
            self.failures.append(failure)
            if self.strict:
                raise DiagnosticSinkError(str(error)) from error

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        try:
            if hasattr(self.logger, "close"):
                self.logger.close()
            if self.tracer is not None and hasattr(self.tracer, "close"):
                self.tracer.close()
        except Exception as error:
            failure = DiagnosticFailure(self.sink_name, "close", type(error).__name__, str(error))
            self.failures.append(failure)
            if self.strict:
                raise DiagnosticSinkError(str(error)) from error
        self._closed = True


LegacyAdapter = LegacyShadowSink


class CompositeEventSink:
    """Canonical-first composite with fail-closed/fail-open boundaries."""

    def __init__(
        self,
        canonical: EventSink,
        diagnostics: list[EventSink] | tuple[EventSink, ...] = (),
        *,
        continue_on_diagnostic_failure: bool = True,
        redaction_policy: RedactionPolicy | None = None,
    ) -> None:
        self.canonical = canonical
        self.diagnostics_sinks = tuple(diagnostics)
        self.continue_on_diagnostic_failure = continue_on_diagnostic_failure
        self.redaction_policy = redaction_policy or RedactionPolicy()
        self.failures: list[DiagnosticFailure] = []

    @property
    def diagnostic_failures(self) -> tuple[DiagnosticFailure, ...]:
        return tuple(self.failures)

    def emit(self, event: RuntimeEvent | dict[str, Any]) -> RuntimeEvent:
        source = _as_event(event)
        clean, _ = redact_event_dict(source.to_dict(), self.redaction_policy)
        clean_event = RuntimeEvent.from_dict(clean)
        try:
            result = self.canonical.emit(clean_event)
        except CanonicalSinkError:
            raise
        except Exception as error:
            raise CanonicalSinkError(str(error)) from error
        canonical_event = result if isinstance(result, RuntimeEvent) else clean_event
        for sink in self.diagnostics_sinks:
            name = getattr(sink, "sink_name", type(sink).__name__)
            try:
                sink.emit(canonical_event)
            except Exception as error:
                failure = DiagnosticFailure(name, canonical_event.id, type(error).__name__, str(error))
                self.failures.append(failure)
                if not self.continue_on_diagnostic_failure:
                    raise DiagnosticSinkError(str(error)) from error
        return canonical_event

    append = emit

    def flush(self) -> None:
        try:
            self.canonical.flush()
        except Exception as error:
            raise CanonicalSinkError(str(error)) from error
        for sink in self.diagnostics_sinks:
            try:
                sink.flush()
            except Exception as error:
                failure = DiagnosticFailure(getattr(sink, "sink_name", type(sink).__name__), "flush", type(error).__name__, str(error))
                self.failures.append(failure)
                if not self.continue_on_diagnostic_failure:
                    raise DiagnosticSinkError(str(error)) from error

    def close(self) -> None:
        try:
            self.canonical.close()
        except Exception as error:
            raise CanonicalSinkError(str(error)) from error
        for sink in self.diagnostics_sinks:
            try:
                sink.close()
            except Exception as error:
                failure = DiagnosticFailure(getattr(sink, "sink_name", type(sink).__name__), "close", type(error).__name__, str(error))
                self.failures.append(failure)
                if not self.continue_on_diagnostic_failure:
                    raise DiagnosticSinkError(str(error)) from error


ShadowEventSink = CompositeEventSink
CompositeSink = CompositeEventSink


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


EventEmitter = RuntimeEventEmitter


__all__ = [
    "CanonicalSink",
    "CanonicalEventSink",
    "CanonicalSinkError",
    "CompositeEventSink",
    "CompositeSink",
    "DiagnosticFailure",
    "DiagnosticSinkError",
    "EventEmitter",
    "EventSink",
    "EventSinkError",
    "LegacyAdapter",
    "LegacyShadowSink",
    "MemoryEventSink",
    "RecordingEventSink",
    "RuntimeEventEmitter",
    "ShadowEventSink",
    "SinkClosedError",
    "legacy_mapping",
]
