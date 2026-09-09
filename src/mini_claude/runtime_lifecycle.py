"""Provider-neutral model and durable tool-boundary orchestration.

The classes here are deliberately independent from the network clients.  A
provider adapter supplies parsed chunks; the recorder owns event vocabulary
and the tool boundary owns the dispatch-before-side-effect invariant.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping

from .archive_capability import ToolResultArchiveCapability
from .artifact_archive import ArtifactArchive
from .event_ids import IdentityFactory, RunContext
from .event_sink import CanonicalToolCallConflictError, RuntimeEventEmitter
from .redaction import RedactionPolicy, redact_payload
from .runtime_event import RuntimeEvent, canonical_json_bytes
from .tool_call_identity import decode_tool_arguments
from .tool_result import (
    ToolResultLimitError,
    ToolResultSerializationError,
    canonical_tool_result,
    parsed_result_error,
    validate_tool_result_size,
)

RUNTIME_PARTIAL_FLUSH_INTERVAL_MS = 80
RUNTIME_PARTIAL_BATCH_MAX_BYTES = 8 * 1024


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def request_shape_hash(request: Mapping[str, Any], *, policy: RedactionPolicy | None = None) -> str:
    clean = redact_payload(dict(request), policy)
    return "sha256:" + hashlib.sha256(canonical_json_bytes(clean)).hexdigest()


@dataclass(frozen=True, slots=True)
class ModelCallSummary:
    request_id: str
    provider: str
    model: str
    attempt: int
    finish_reason: str | None
    latency_ms: int
    usage: Mapping[str, int | None]
    error_type: str | None = None
    attempt_id: str | None = None


class ToolOperationConflictError(RuntimeError):
    """A provider call identity was reused with different canonical arguments."""


class UncertainToolOperationError(RuntimeError):
    """A dispatched operation has no durable outcome and cannot be replayed."""


class ModelCallRecorder:
    """Turn provider-specific chunks into one canonical lifecycle."""

    def __init__(
        self,
        emitter: RuntimeEventEmitter,
        context: RunContext,
        *,
        provider: str,
        model: str,
        id_factory: IdentityFactory | None = None,
        clock: Callable[[], int | float | datetime] | None = None,
        max_partial_chars: int = 4_096,
        partial_flush_interval_ms: int = RUNTIME_PARTIAL_FLUSH_INTERVAL_MS,
        partial_batch_max_bytes: int = RUNTIME_PARTIAL_BATCH_MAX_BYTES,
        redaction_policy: RedactionPolicy | None = None,
    ) -> None:
        self.emitter = emitter
        self.context = context
        self.provider = provider
        self.model = model
        self.ids = id_factory or IdentityFactory()
        self.clock = clock or _now_ms
        self.max_partial_chars = max_partial_chars
        if partial_flush_interval_ms <= 0:
            raise ValueError("partial_flush_interval_ms must be positive")
        if partial_batch_max_bytes <= 0:
            raise ValueError("partial_batch_max_bytes must be positive")
        self.partial_flush_interval_ms = partial_flush_interval_ms
        self.partial_batch_max_bytes = partial_batch_max_bytes
        self.redaction_policy = redaction_policy or RedactionPolicy()
        self.request_id: str | None = None
        self.attempt = 0
        self.attempt_id: str | None = None
        self._started_at = 0.0
        self._finished = False
        self.events: list[RuntimeEvent] = []
        self._partial_buffers: dict[str, list[RuntimeEvent]] = {}
        self._partial_arrival_order: list[RuntimeEvent] = []
        self._partial_buffer_bytes = 0
        self._partial_streams: set[str] = set()
        self._partial_sequences: dict[str, int] = {}
        self._partial_timer: asyncio.TimerHandle | None = None
        self._partial_flush_error: BaseException | None = None

    def _timestamp(self) -> int:
        value = self.clock()
        if isinstance(value, datetime):
            instant = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return int(instant.astimezone(timezone.utc).timestamp() * 1000)
        if isinstance(value, float):
            return int(value * 1000) if value < 10_000_000_000 else int(value)
        return int(value)

    def _emit(
        self,
        *,
        role: str,
        author: str,
        content: Mapping[str, Any] | None = None,
        actions: Mapping[str, Any] | None = None,
        refs: Mapping[str, Any] | None = None,
        status: str | None = None,
        partial: bool = False,
        metadata: Mapping[str, Any] | None = None,
        clear_partial_stream_key: str | None = None,
        clear_partial_invocation: bool = False,
    ) -> RuntimeEvent:
        event = RuntimeEvent.create(
            self.context,
            role=role,
            author=author,
            partial=partial,
            ts=self._timestamp(),
            event_id=self.ids.event_id(),
            content=content,
            actions=actions,
            refs=refs,
            status=status,
            metadata={
                "provider": self.provider,
                "model": self.model,
                "request_id": self.request_id,
                "attempt": self.attempt,
                "attempt_id": self.attempt_id,
                **dict(metadata or {}),
            },
        )
        if partial:
            return self._emit_partial_event(event)
        if self.emitter.partial_batch_supported is False:
            # A legacy sink has no mutable partial boundary of its own.  Do
            # not let a pending partial overtake any ordinary or terminal
            # lifecycle event when we fall back to generic emit().
            self.flush_partials()
        clear_keys = (
            (clear_partial_stream_key,)
            if clear_partial_stream_key is not None
            else ()
        )
        if clear_partial_invocation or clear_keys:
            self.flush_partials(stream_keys=None if clear_partial_invocation else clear_keys)
            persisted = self.emitter.emit_final(
                event,
                clear_partial_stream_keys=clear_keys,
                clear_partial_invocation=clear_partial_invocation,
            )
            self._forget_partial_streams(
                stream_keys=None if clear_partial_invocation else clear_keys,
            )
        else:
            persisted = self.emitter.emit(event)
        event = persisted if isinstance(persisted, RuntimeEvent) else event
        self.events.append(event)
        return event

    def _stream_key(self, kind: str, call_id: str | None = None) -> str:
        attempt_id = self.attempt_id or f"attempt-{self.attempt}"
        return ":".join(
            (
                "partial",
                self.context.invocation_id,
                attempt_id,
                kind,
                call_id or "",
            )
        )

    def _next_partial_sequence(self, stream_key: str) -> int:
        sequence = self._partial_sequences.get(stream_key, 0) + 1
        self._partial_sequences[stream_key] = sequence
        return sequence

    @staticmethod
    def _event_stream_key(event: RuntimeEvent) -> str:
        metadata = event.metadata or {}
        value = metadata.get("partial_stream_key")
        if isinstance(value, str) and value.strip():
            return value
        content = event.content or {}
        call_id = content.get("id") if content.get("kind") == "function_call" else ""
        attempt_id = metadata.get("attempt_id") or ""
        return ":".join(
            ("partial", event.invocation_id, str(attempt_id), str(content.get("kind") or ""), str(call_id or ""))
        )

    def _raise_partial_flush_error(self) -> None:
        if self._partial_flush_error is None:
            return
        error = self._partial_flush_error
        self._partial_flush_error = None
        raise error

    def _cancel_partial_timer(self) -> None:
        if self._partial_timer is not None:
            self._partial_timer.cancel()
            self._partial_timer = None

    def _schedule_partial_flush(self) -> None:
        if self._partial_timer is not None or not self._partial_buffers:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._partial_timer = loop.call_later(
            self.partial_flush_interval_ms / 1000,
            self._on_partial_timer,
        )

    def _on_partial_timer(self) -> None:
        self._partial_timer = None
        try:
            self.flush_partials()
        except BaseException as error:
            # A synchronous callback cannot propagate into the provider async
            # iterator. Preserve the failure and surface it at the next
            # recorder operation or explicit flush instead.
            self._partial_flush_error = error

    def _emit_partial_event(self, event: RuntimeEvent) -> RuntimeEvent:
        self._raise_partial_flush_error()
        stream_key = self._event_stream_key(event)
        if stream_key not in self._partial_streams:
            # Maka closes the previous buffered stream before establishing a
            # new stream anchor. This also prevents mixed text/tool streams.
            try:
                self.flush_partials()
                persisted = self.emitter.emit_partial_batch([event])
            except BaseException:
                # The anchor did not become visible to this recorder. Reset
                # its sequence so a retry can start at one; an uncertain
                # SQLite commit remains fail-closed because the store will
                # reject a conflicting committed sequence instead of appending
                # a second anchor.
                self._partial_sequences.pop(stream_key, None)
                raise
            if not persisted:
                self._partial_sequences.pop(stream_key, None)
                raise RuntimeError("partial batch emitter returned no event")
            self._partial_streams.add(stream_key)
            self.events.append(persisted[0])
            self._schedule_partial_flush()
            return persisted[0]

        self.events.append(event)
        self._partial_buffers.setdefault(stream_key, []).append(event)
        self._partial_arrival_order.append(event)
        self._partial_buffer_bytes += len(event.canonical_bytes())
        if self._partial_buffer_bytes >= self.partial_batch_max_bytes:
            self.flush_partials()
        else:
            self._schedule_partial_flush()
        return event

    def flush_partials(self, *, stream_keys: Iterable[str] | None = None) -> None:
        """Flush buffered streaming observations without changing final facts."""

        if stream_keys is None:
            selected = set(self._partial_buffers)
        else:
            selected = {str(key) for key in stream_keys}
        if self.emitter.partial_batch_supported is False:
            # Generic sinks cannot perform selective snapshot cleanup.  Flush
            # every pending partial first so no other stream can appear after
            # a final event that arrived later.
            selected = set(self._partial_buffers)
        pending = [
            event
            for event in self._partial_arrival_order
            if self._event_stream_key(event) in selected
        ]
        if not pending:
            if not self._partial_buffers:
                self._partial_flush_error = None
                self._cancel_partial_timer()
            return
        self.emitter.emit_partial_batch(pending)
        flushed_ids = {event.id for event in pending}
        self._partial_arrival_order = [
            event
            for event in self._partial_arrival_order
            if event.id not in flushed_ids
        ]
        for key in selected:
            self._partial_buffers.pop(key, None)
        self._partial_buffer_bytes = sum(
            len(event.canonical_bytes())
            for events in self._partial_buffers.values()
            for event in events
        )
        if not self._partial_buffers:
            # A successful explicit retry has recovered any timer failure
            # marker associated with the drained buffers.  Do not replay a
            # stale error on the next delta or provider retry.
            self._partial_flush_error = None
            self._cancel_partial_timer()
        else:
            self._schedule_partial_flush()

    def _forget_partial_streams(
        self, *, stream_keys: Iterable[str] | None = None
    ) -> None:
        if stream_keys is None:
            keys = set(self._partial_streams)
        else:
            keys = {str(key) for key in stream_keys}
        self._partial_streams.difference_update(keys)
        self._partial_arrival_order = [
            event
            for event in self._partial_arrival_order
            if self._event_stream_key(event) not in keys
        ]
        for key in keys:
            self._partial_sequences.pop(key, None)
            self._partial_buffers.pop(key, None)
        self._partial_buffer_bytes = sum(
            len(event.canonical_bytes())
            for events in self._partial_buffers.values()
            for event in events
        )
        if not self._partial_buffers:
            self._cancel_partial_timer()

    def start(
        self,
        request_id: str | None = None,
        *,
        attempt: int = 1,
        request: Mapping[str, Any] | None = None,
    ) -> RuntimeEvent:
        if self.request_id is not None and not self._finished:
            raise RuntimeError("model call already started")
        self.request_id = request_id or self.ids.new("request")
        self.attempt = attempt
        self.attempt_id = self.ids.new("attempt")
        self._started_at = time.monotonic()
        self._finished = False
        metadata: dict[str, Any] = {"lifecycle": "invocation_opened"}
        if request is not None:
            metadata["request_shape_hash"] = request_shape_hash(request, policy=self.redaction_policy)
        return self._emit(
            role="system",
            author="agent",
            content={
                "kind": "invocation_opened",
                "protocol": "invocation_opened_v1",
                "route": {"provider": self.provider, "model": self.model},
                "configuration": {
                    "attempt": attempt,
                    "request_shape_hash": metadata.get("request_shape_hash"),
                },
                "root": {"kind": "agent"},
                "source": {"kind": "fresh"},
                **(
                    {"lineage": {"parent_run_id": self.context.parent_run_id}}
                    if self.context.parent_run_id
                    else {}
                ),
            },
            status="streaming",
            metadata=metadata,
        )

    def partial_text(self, text: str, *, kind: str = "text") -> RuntimeEvent:
        self._require_started()
        # Surface a failed timer flush before allocating the next sequence.
        # A rejected delta must not create a sequence gap after recovery.
        self._raise_partial_flush_error()
        if kind not in {"text", "thinking"}:
            raise ValueError("partial text kind must be text or thinking")
        stream_key = self._stream_key(kind)
        partial_seq = self._next_partial_sequence(stream_key)
        bounded = text
        metadata: dict[str, Any] = {
            "lifecycle": "stream_partial",
            "partial_stream_key": stream_key,
            "partial_seq": partial_seq,
        }
        if len(text) > self.max_partial_chars:
            bounded = text[: self.max_partial_chars]
            metadata.update(
                {
                    "bounded": True,
                    "original_chars": len(text),
                    "original_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
        return self._emit(
            role="model",
            author="agent",
            content={"kind": kind, "text": bounded},
            partial=True,
            metadata=metadata,
        )

    def partial_tool_arguments(self, call_id: str, name: str, fragment: str) -> RuntimeEvent:
        self._require_started()
        self._raise_partial_flush_error()
        stream_key = self._stream_key("function_call", call_id)
        partial_seq = self._next_partial_sequence(stream_key)
        return self._emit(
            role="model",
            author="agent",
            content={"kind": "function_call", "id": call_id, "name": name, "args": fragment},
            partial=True,
            refs={"tool_call_id": call_id},
            metadata={
                "lifecycle": "tool_arguments_partial",
                "partial_stream_key": stream_key,
                "partial_seq": partial_seq,
            },
        )

    def final_text(self, text: str) -> RuntimeEvent:
        self._require_started()
        stream_key = self._stream_key("text")
        return self._emit(
            role="model",
            author="agent",
            content={"kind": "text", "text": text},
            metadata={"lifecycle": "model_final"},
            clear_partial_stream_key=stream_key,
        )

    def final_thinking(self, text: str, *, signature: str | None = None) -> RuntimeEvent:
        """Persist provider thinking exactly enough for a later replay."""

        self._require_started()
        stream_key = self._stream_key("thinking")
        content: dict[str, Any] = {"kind": "thinking", "text": text}
        if signature is not None:
            content["signature"] = signature
        return self._emit(
            role="model",
            author="agent",
            content=content,
            metadata={"lifecycle": "model_final"},
            clear_partial_stream_key=stream_key,
        )

    def final_tool_call(self, call_id: str, name: str, arguments: Any) -> RuntimeEvent:
        self._require_started()
        safe_args = redact_payload(arguments, self.redaction_policy)
        stream_key = self._stream_key("function_call", call_id)
        return self._emit(
            role="model",
            author="agent",
            content={"kind": "function_call", "id": call_id, "name": name, "args": safe_args},
            refs={"tool_call_id": call_id},
            metadata={"lifecycle": "tool_call_final"},
            clear_partial_stream_key=stream_key,
        )

    def usage(self, usage: Mapping[str, Any] | None) -> RuntimeEvent:
        self._require_started()
        known: dict[str, int | None] = {}
        usage_status = "unknown"
        if isinstance(usage, Mapping):
            for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens"):
                if name in usage and usage[name] is not None:
                    try:
                        known[name] = int(usage[name])
                    except (TypeError, ValueError):
                        known[name] = None
            usage_status = "complete" if {"input_tokens", "output_tokens"} <= set(known) else "partial"
        return self._emit(
            role="system",
            author="agent",
            actions={"usage": known},
            metadata={"lifecycle": "usage", "usage_status": usage_status},
        )

    def finish(
        self,
        finish_reason: str,
        *,
        usage: Mapping[str, Any] | None = None,
        latency_ms: int | None = None,
    ) -> ModelCallSummary:
        self._require_started()
        self.usage(usage)
        latency = latency_ms if latency_ms is not None else int((time.monotonic() - self._started_at) * 1000)
        self._emit(
            role="system",
            author="agent",
            actions={"model_finish": {"finish_reason": finish_reason, "latency_ms": latency}},
            metadata={"lifecycle": "model_final"},
            clear_partial_invocation=True,
        )
        self._finished = True
        normalised_usage: dict[str, int | None] = {
            name: None for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens")
        }
        if isinstance(usage, Mapping):
            for name in normalised_usage:
                if usage.get(name) is not None:
                    try:
                        normalised_usage[name] = int(usage[name])
                    except (TypeError, ValueError):
                        pass
        return ModelCallSummary(
            self.request_id or "",
            self.provider,
            self.model,
            self.attempt,
            finish_reason,
            latency,
            normalised_usage,
            attempt_id=self.attempt_id,
        )

    def retry(self, *, reason: str, attempt: int | None = None) -> RuntimeEvent:
        """Record an explicit provider retry while retaining prior attempt facts."""

        self._require_started()
        self.flush_partials()
        next_attempt = attempt if attempt is not None else self.attempt + 1
        previous_attempt_id = self.attempt_id
        self.attempt = next_attempt
        self.attempt_id = self.ids.new("attempt")
        self._started_at = time.monotonic()
        return self._emit(
            role="system",
            author="agent",
            actions={
                "attempt_retry": {
                    "attempt": next_attempt,
                    "attempt_id": self.attempt_id,
                    "previous_attempt_id": previous_attempt_id,
                    "reason": reason,
                }
            },
            metadata={
                "lifecycle": "attempt_retry",
                "attempt_id": self.attempt_id,
                "previous_attempt_id": previous_attempt_id,
                "retry_reason": reason,
            },
        )

    def error(self, error: BaseException, *, usage: Mapping[str, Any] | None = None) -> ModelCallSummary:
        self._require_started()
        self.usage(usage)
        latency = int((time.monotonic() - self._started_at) * 1000)
        error_type = type(error).__name__
        error_code = getattr(error, "code", error_type)
        self._emit(
            role="system",
            author="system",
            content={"kind": "error", "code": error_code, "message": str(error)},
            status="failed",
            metadata={"lifecycle": "provider_error", "error_type": error_type},
            clear_partial_invocation=True,
        )
        self._finished = True
        return ModelCallSummary(
            self.request_id or "",
            self.provider,
            self.model,
            self.attempt,
            None,
            latency,
            {},
            error_type,
            self.attempt_id,
        )

    def budget_exceeded(self, reason: str) -> RuntimeEvent:
        self._require_started()
        event = self._emit(
            role="system",
            author="system",
            content={"kind": "error", "code": "budget_exceeded", "message": reason},
            status="budget_exceeded",
            metadata={"lifecycle": "budget"},
            clear_partial_invocation=True,
        )
        self._finished = True
        return event

    def _require_started(self) -> None:
        if self.request_id is None:
            raise RuntimeError("model call has not started")
        if self._finished:
            raise RuntimeError("model call has already finished")


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    call_id: str
    name: str
    result: Any
    success: bool
    executed: bool
    error_type: str | None = None
    denied: bool = False
    cancelled: bool = False
    operation_id: str | None = None


class DurableToolBoundary:
    """Guarantee dispatch durability before invoking a tool callable."""

    def __init__(
        self,
        emitter: RuntimeEventEmitter,
        context: RunContext,
        *,
        id_factory: IdentityFactory | None = None,
        redaction_policy: RedactionPolicy | None = None,
        max_result_bytes: int = 16_384,
        artifact_archive: ArtifactArchive | None = None,
        archive_capability: ToolResultArchiveCapability | None = None,
    ) -> None:
        self.emitter = emitter
        self.context = context
        self.ids = id_factory or IdentityFactory()
        self.redaction_policy = redaction_policy or RedactionPolicy()
        self.max_result_bytes = max_result_bytes
        self.artifact_archive = artifact_archive
        self.archive_capability = archive_capability
        self.execution_count = 0

    def _event(
        self,
        *,
        role: str,
        author: str,
        content: Mapping[str, Any] | None = None,
        actions: Mapping[str, Any] | None = None,
        call_id: str | None = None,
        operation_id: str | None = None,
        status: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeEvent:
        event = RuntimeEvent.create(
            self.context,
            role=role,
            author=author,
            ts=_now_ms(),
            event_id=self.ids.event_id(),
            content=content,
            actions=actions,
            refs={
                **({"tool_call_id": call_id} if call_id else {}),
                **({"operation_id": operation_id} if operation_id else {}),
            } or None,
            status=status,
            metadata=metadata,
        )
        persisted = self.emitter.emit(event)
        return persisted if isinstance(persisted, RuntimeEvent) else event

    async def execute(
        self,
        *,
        call_id: str,
        name: str,
        arguments: Mapping[str, Any] | Any,
        permission: Mapping[str, Any] | str = "allow",
        executor: Callable[[], Any] | Callable[[], Awaitable[Any]],
        timeout: float | None = None,
        on_started: Callable[[], Any] | None = None,
        recovery_mode: str = "manual_on_unknown",
    ) -> ToolExecutionResult:
        decoded_arguments, argument_error = decode_tool_arguments(arguments)
        safe_arguments = redact_payload(decoded_arguments, self.redaction_policy)
        try:
            self._event(
                role="model",
                author="agent",
                content={"kind": "function_call", "id": call_id, "name": name, "args": safe_arguments},
                call_id=call_id,
                metadata={"lifecycle": "tool_call_final"},
            )
        except CanonicalToolCallConflictError as error:
            raise ToolOperationConflictError(str(error)) from error
        if argument_error:
            self._event(
                role="system",
                author="system",
                content={"kind": "error", "code": "invalid_tool_arguments", "message": argument_error},
                call_id=call_id,
                metadata={"lifecycle": "tool_validation_error"},
            )
            result = f"Error: {argument_error}"
            self._outcome(call_id, name, result, success=False, executed=False, error_type="ValidationError")
            return ToolExecutionResult(call_id, name, result, False, False, "ValidationError")
        decision, reason = self._permission(permission)
        self._event(
            role="system",
            author="system",
            actions={"permission": {"decision": decision, "reason": reason}},
            call_id=call_id,
            metadata={"lifecycle": "permission"},
        )
        if decision != "allow":
            result = f"Action denied: {reason}" if reason else "Action denied."
            self._outcome(call_id, name, result, success=False, executed=False, denied=True)
            return ToolExecutionResult(call_id, name, result, False, False, denied=True)

        args_digest = hashlib.sha256(canonical_json_bytes(safe_arguments)).hexdigest()
        operation_id = "op-" + hashlib.sha256(
            f"{self.context.invocation_id}\0{call_id}\0{args_digest}".encode("utf-8")
        ).hexdigest()[:32]
        existing = self.emitter.read_tool_operation_for_run_call(
            self.context.run_id, call_id
        )
        if existing is None:
            existing = self.emitter.read_tool_operation_for_call(
                self.context.invocation_id, call_id
            )
        if existing is not None:
            if (
                existing.session_id != self.context.session_id
                or existing.run_id != self.context.run_id
                or existing.provider_tool_call_id != call_id
                or existing.tool_name != name
                or existing.canonical_args_hash != f"sha256:{args_digest}"
                or existing.recovery_mode != recovery_mode
            ):
                raise ToolOperationConflictError(
                    f"provider tool call {call_id} has conflicting operation identity"
                )
            if existing.state == "outcome_unknown":
                raise UncertainToolOperationError(
                    f"operation {existing.operation_id} has an unknown outcome; explicit new invocation required"
                )
            if existing.state in {"completed", "failed", "denied", "cancelled"}:
                return ToolExecutionResult(
                    call_id,
                    name,
                    existing.result,
                    bool(existing.success),
                    bool(existing.executed),
                    existing.error_type,
                    denied=existing.state == "denied",
                    operation_id=existing.operation_id,
                )
        # This call is the durable barrier.  If it raises, executor is never
        # reached and the caller must classify the run as uncertain/failing.
        self._event(
            role="system",
            author="system",
            actions={
                "tool_dispatch": {
                    "protocol": "tool_dispatch_v1",
                    "operation_id": operation_id,
                    "provider_tool_call_id": call_id,
                    "tool_name": name,
                    "name": name,
                    "canonical_args_hash": f"sha256:{args_digest}",
                    "recovery_mode": recovery_mode,
                }
            },
            call_id=call_id,
            operation_id=operation_id,
            metadata={"lifecycle": "tool_dispatch", "dispatch_durable": True},
        )
        if on_started is not None:
            maybe = on_started()
            if inspect.isawaitable(maybe):
                await maybe
        self.execution_count += 1
        execution_started_at = time.monotonic()
        try:
            value = executor()
            if inspect.isawaitable(value):
                value = await asyncio.wait_for(value, timeout) if timeout is not None else await value
            result_value: Any = value
            error_type: str | None = None
            try:
                validate_tool_result_size(value, name)
                result_value = canonical_tool_result(value)
            except (ToolResultLimitError, ToolResultSerializationError) as error:
                result_value = error.payload()
                error_type = error.code
                success = False
            else:
                public_error = parsed_result_error(value)
                if public_error is not None:
                    result_value = dict(public_error)
                    error_type = str(public_error.get("error_type") or "ToolResultError")
                    success = False
                else:
                    success = not (isinstance(value, str) and value.startswith("Error:"))
            self._outcome(
                call_id,
                name,
                result_value,
                success=success,
                executed=True,
                error_type=error_type,
                operation_id=operation_id,
                duration_ms=int((time.monotonic() - execution_started_at) * 1000),
            )
            return ToolExecutionResult(
                call_id, name, result_value, success, True, error_type,
                operation_id=operation_id,
            )
        except asyncio.CancelledError:
            self._outcome(
                call_id, name, "tool cancelled", success=False, executed=True,
                error_type="CancelledError", operation_id=operation_id,
                duration_ms=int((time.monotonic() - execution_started_at) * 1000),
            )
            raise
        except asyncio.TimeoutError:
            self._outcome(
                call_id, name, "tool timed out", success=False, executed=True,
                error_type="TimeoutError", operation_id=operation_id,
                duration_ms=int((time.monotonic() - execution_started_at) * 1000),
            )
            return ToolExecutionResult(
                call_id, name, "tool timed out", False, True, "TimeoutError",
                operation_id=operation_id,
            )
        except Exception as error:
            message = f"Error: {error}"
            self._outcome(
                call_id, name, message, success=False, executed=True,
                error_type=type(error).__name__, operation_id=operation_id,
                duration_ms=int((time.monotonic() - execution_started_at) * 1000),
            )
            return ToolExecutionResult(
                call_id, name, message, False, True, type(error).__name__,
                operation_id=operation_id,
            )

    def _outcome(
        self,
        call_id: str,
        name: str,
        result: Any,
        *,
        success: bool,
        executed: bool,
        denied: bool = False,
        error_type: str | None = None,
        operation_id: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        # The enclosing RuntimeEvent redaction pass is path-aware and sees
        # this value at ``content.result``.  Redacting here first would move
        # it to the root path and incorrectly create an ``inline:*`` ref.
        safe_result = result
        action = {"name": name, "success": success, "executed": executed}
        if operation_id:
            action.update(
                {
                    "operation_id": operation_id,
                    "provider_tool_call_id": call_id,
                    "tool_name": name,
                }
            )
        if duration_ms is not None:
            action["duration_ms"] = max(int(duration_ms), 0)
        if error_type:
            action["error_type"] = error_type
        self._event(
            role="tool",
            author="tool",
            content={
                "kind": "function_response",
                "id": call_id,
                "name": name,
                "result": safe_result,
                "isError": not success,
            },
            actions={"tool_outcome": action},
            call_id=call_id,
            operation_id=operation_id,
            metadata={"lifecycle": "tool_outcome", "denied": denied},
        )
        self._event(
            role="tool",
            author="tool",
            content={
                "kind": "function_response",
                "id": call_id,
                "name": name,
                "result": safe_result,
                "isError": not success,
            },
            call_id=call_id,
            operation_id=operation_id,
            metadata={"lifecycle": "function_response", "executed": executed},
        )

    @staticmethod
    def _permission(permission: Mapping[str, Any] | str) -> tuple[str, str]:
        if isinstance(permission, Mapping):
            decision = permission.get("decision", permission.get("action", "unknown"))
            reason = str(permission.get("reason", permission.get("message", "")))
        else:
            decision, reason = permission, ""
        decision = str(decision).lower()
        if decision in {"allow", "allowed", "approve", "approved"}:
            return "allow", reason
        if decision in {"deny", "denied", "reject", "rejected"}:
            return "deny", reason
        return "unknown", reason or "permission decision is not known"


__all__ = [
    "DurableToolBoundary",
    "ModelCallRecorder",
    "ModelCallSummary",
    "RUNTIME_PARTIAL_BATCH_MAX_BYTES",
    "RUNTIME_PARTIAL_FLUSH_INTERVAL_MS",
    "ToolExecutionResult",
    "ToolOperationConflictError",
    "UncertainToolOperationError",
    "request_shape_hash",
    "decode_tool_arguments",
]
