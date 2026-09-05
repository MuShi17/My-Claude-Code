"""Provider-neutral model and durable tool-boundary orchestration.

The classes here are deliberately independent from the network clients.  A
provider adapter supplies parsed chunks; the recorder owns event vocabulary
and the tool boundary owns the dispatch-before-side-effect invariant.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from .artifact_archive import ArtifactArchive, ArtifactArchiveError
from .event_ids import IdentityFactory, RunContext
from .event_sink import RuntimeEventEmitter
from .redaction import RedactionPolicy, bound_payload, redact_payload
from .runtime_event import RuntimeEvent, canonical_json_bytes


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def request_shape_hash(request: Mapping[str, Any], *, policy: RedactionPolicy | None = None) -> str:
    clean = redact_payload(dict(request), policy)
    return "sha256:" + hashlib.sha256(canonical_json_bytes(clean)).hexdigest()


def decode_tool_arguments(raw: Mapping[str, Any] | str | Any) -> tuple[Any, str | None]:
    """Decode a final provider tool payload without treating bad JSON as ``{}``."""

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
        redaction_policy: RedactionPolicy | None = None,
    ) -> None:
        self.emitter = emitter
        self.context = context
        self.provider = provider
        self.model = model
        self.ids = id_factory or IdentityFactory()
        self.clock = clock or _now_ms
        self.max_partial_chars = max_partial_chars
        self.redaction_policy = redaction_policy or RedactionPolicy()
        self.request_id: str | None = None
        self.attempt = 0
        self.attempt_id: str | None = None
        self._started_at = 0.0
        self._finished = False
        self.events: list[RuntimeEvent] = []

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
        persisted = self.emitter.emit(event)
        event = persisted if isinstance(persisted, RuntimeEvent) else event
        self.events.append(event)
        return event

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
            content={"kind": "invocation_opened"},
            status="streaming",
            metadata=metadata,
        )

    def partial_text(self, text: str) -> RuntimeEvent:
        self._require_started()
        bounded = text
        metadata: dict[str, Any] = {"lifecycle": "stream_partial"}
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
            content={"kind": "text", "text": bounded},
            partial=True,
            metadata=metadata,
        )

    def partial_tool_arguments(self, call_id: str, name: str, fragment: str) -> RuntimeEvent:
        self._require_started()
        return self._emit(
            role="model",
            author="agent",
            content={"kind": "function_call", "id": call_id, "name": name, "args": fragment},
            partial=True,
            refs={"tool_call_id": call_id},
            metadata={"lifecycle": "tool_arguments_partial"},
        )

    def final_text(self, text: str) -> RuntimeEvent:
        self._require_started()
        return self._emit(
            role="model",
            author="agent",
            content={"kind": "text", "text": text},
            metadata={"lifecycle": "model_final"},
        )

    def final_tool_call(self, call_id: str, name: str, arguments: Any) -> RuntimeEvent:
        self._require_started()
        safe_args = redact_payload(arguments, self.redaction_policy)
        return self._emit(
            role="model",
            author="agent",
            content={"kind": "function_call", "id": call_id, "name": name, "args": safe_args},
            refs={"tool_call_id": call_id},
            metadata={"lifecycle": "tool_call_final"},
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
        self._emit(
            role="system",
            author="system",
            content={"kind": "error", "code": error_type, "message": str(error)},
            status="failed",
            metadata={"lifecycle": "provider_error", "error_type": error_type},
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
        self._finished = True
        return self._emit(
            role="system",
            author="system",
            content={"kind": "error", "code": "budget_exceeded", "message": reason},
            status="budget_exceeded",
            metadata={"lifecycle": "budget"},
        )

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
    ) -> None:
        self.emitter = emitter
        self.context = context
        self.ids = id_factory or IdentityFactory()
        self.redaction_policy = redaction_policy or RedactionPolicy()
        self.max_result_bytes = max_result_bytes
        self.artifact_archive = artifact_archive
        self.execution_count = 0

    def _event(
        self,
        *,
        role: str,
        author: str,
        content: Mapping[str, Any] | None = None,
        actions: Mapping[str, Any] | None = None,
        call_id: str | None = None,
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
            refs={"tool_call_id": call_id} if call_id else None,
            status=status,
            metadata=metadata,
        )
        self.emitter.emit(event)
        return event

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
    ) -> ToolExecutionResult:
        decoded_arguments, argument_error = decode_tool_arguments(arguments)
        safe_arguments = redact_payload(decoded_arguments, self.redaction_policy)
        self._event(
            role="model",
            author="agent",
            content={"kind": "function_call", "id": call_id, "name": name, "args": safe_arguments},
            call_id=call_id,
            metadata={"lifecycle": "tool_call_final"},
        )
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
        # This call is the durable barrier.  If it raises, executor is never
        # reached and the caller must classify the run as uncertain/failing.
        self._event(
            role="system",
            author="system",
            actions={"tool_dispatch": {"name": name, "arguments_digest": f"sha256:{args_digest}"}},
            call_id=call_id,
            metadata={"lifecycle": "tool_dispatch", "dispatch_durable": True},
        )
        if on_started is not None:
            maybe = on_started()
            if inspect.isawaitable(maybe):
                await maybe
        self.execution_count += 1
        try:
            value = executor()
            if inspect.isawaitable(value):
                value = await asyncio.wait_for(value, timeout) if timeout is not None else await value
            bounded, archive_error = self._bound_result(value, call_id=call_id, name=name)
            success = not (isinstance(value, str) and value.startswith("Error:"))
            if archive_error is not None:
                success = False
            self._outcome(
                call_id,
                name,
                bounded,
                success=success,
                executed=True,
                error_type=type(archive_error).__name__ if archive_error else None,
            )
            return ToolExecutionResult(
                call_id, name, bounded, success, True,
                type(archive_error).__name__ if archive_error else None,
            )
        except asyncio.CancelledError:
            self._outcome(call_id, name, "tool cancelled", success=False, executed=True, error_type="CancelledError")
            raise
        except asyncio.TimeoutError:
            self._outcome(call_id, name, "tool timed out", success=False, executed=True, error_type="TimeoutError")
            return ToolExecutionResult(call_id, name, "tool timed out", False, True, "TimeoutError")
        except Exception as error:
            message = f"Error: {error}"
            self._outcome(call_id, name, message, success=False, executed=True, error_type=type(error).__name__)
            return ToolExecutionResult(call_id, name, message, False, True, type(error).__name__)

    def _bound_result(
        self,
        value: Any,
        *,
        call_id: str,
        name: str,
    ) -> tuple[Any, Exception | None]:
        policy = RedactionPolicy(
            version=self.redaction_policy.version,
            max_inline_bytes=self.max_result_bytes,
            max_string_chars=self.redaction_policy.max_string_chars,
        )
        try:
            encoded = (
                value.encode("utf-8") if isinstance(value, str)
                else canonical_json_bytes(value)
            )
        except (TypeError, ValueError):
            encoded = str(value).encode("utf-8")
        if len(encoded) <= self.max_result_bytes:
            return bound_payload(value, ref=f"tool-result:{call_id}", policy=policy), None
        if self.artifact_archive is None:
            return {
                "kind": "archive_error",
                "error_type": "ArtifactArchiveUnavailable",
                "message": "large tool result cannot be referenced without an artifact archive",
                "size_bytes": len(encoded),
                "tool_name": name,
            }, ArtifactArchiveError("artifact archive is not configured")
        try:
            archived = self.artifact_archive.archive(
                value,
                mime_type="text/plain" if isinstance(value, str) else "application/json",
                encoding="utf-8" if isinstance(value, str) else "binary",
                scope="tool-result",
                redaction_policy=self.redaction_policy,
                metadata={"call_id": call_id, "tool_name": name},
            )
            return archived.placeholder(), None
        except Exception as error:
            return {
                "kind": "archive_error",
                "error_type": type(error).__name__,
                "message": str(error),
                "size_bytes": len(encoded),
                "tool_name": name,
            }, error

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
    ) -> None:
        safe_result = redact_payload(result, self.redaction_policy)
        action = {"name": name, "success": success, "executed": executed}
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
    "ToolExecutionResult",
    "request_shape_hash",
    "decode_tool_arguments",
]
