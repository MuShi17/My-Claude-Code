"""Run/attempt lifecycle guards shared by main and child agents."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping

from .event_ids import IdentityFactory, RunContext
from .event_sink import RuntimeEventEmitter
from .runtime_event import RuntimeEvent

NON_TERMINAL_STATES = frozenset({"open", "running", "awaiting-tool"})
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "aborted", "budget_exceeded"})
ALL_STATES = NON_TERMINAL_STATES | TERMINAL_STATES


class LifecycleError(RuntimeError):
    code = "lifecycle_error"


class InvalidTransitionError(LifecycleError):
    code = "invalid_transition"


class LateEventError(LifecycleError):
    code = "late_event"


class TerminalConflictError(LifecycleError):
    code = "terminal_conflict"


@dataclass(frozen=True, slots=True)
class Attempt:
    attempt_id: str
    number: int
    reason: str | None
    status: str = "open"


@dataclass(frozen=True, slots=True)
class LifecycleState:
    run_id: str
    parent_run_id: str | None
    status: str
    attempt_number: int
    terminal_event_id: str | None = None
    terminal_reason: str | None = None


class RunStateGuard:
    """Guard event admission and idempotent terminal finalization for one run."""

    def __init__(
        self,
        context: RunContext,
        emitter: RuntimeEventEmitter,
        *,
        id_factory: IdentityFactory | None = None,
    ) -> None:
        self.context = context
        self.emitter = emitter
        self.ids = id_factory or IdentityFactory()
        self.state = LifecycleState(context.run_id, context.parent_run_id, "open", 1)
        self.attempts: list[Attempt] = []
        self.events: list[RuntimeEvent] = []
        self._terminal_event: RuntimeEvent | None = None

    @property
    def run_id(self) -> str:
        return self.context.run_id

    @property
    def is_terminal(self) -> bool:
        return self.state.status in TERMINAL_STATES

    @property
    def terminal_event(self) -> RuntimeEvent | None:
        return self._terminal_event

    def start(self) -> RuntimeEvent:
        if self.state.status != "open":
            raise InvalidTransitionError(f"cannot start from {self.state.status}")
        return self.transition("running", reason="run_started")

    def awaiting_tool(self) -> RuntimeEvent:
        return self.transition("awaiting-tool", reason="tool_boundary")

    def resume_running(self) -> RuntimeEvent:
        return self.transition("running", reason="tool_completed")

    def transition(self, target: str, *, reason: str | None = None) -> RuntimeEvent:
        current = self.state.status
        if target not in ALL_STATES:
            raise InvalidTransitionError(f"unknown lifecycle state {target}")
        if current in TERMINAL_STATES:
            raise InvalidTransitionError(f"run is already {current}")
        legal = {
            "open": {"running", "failed", "cancelled", "aborted", "budget_exceeded"},
            "running": {"awaiting-tool", "completed", "failed", "cancelled", "aborted", "budget_exceeded"},
            "awaiting-tool": {"running", "completed", "failed", "cancelled", "aborted", "budget_exceeded"},
        }
        if target not in legal[current]:
            raise InvalidTransitionError(f"cannot transition {current} -> {target}")
        if target in TERMINAL_STATES:
            return self.finalize(target, reason=reason)
        self.state = replace(self.state, status=target)
        return self._emit(
            actions={"run_state": {"status": target, "reason": reason}},
            metadata={"lifecycle": "run_state"},
        )

    def new_attempt(self, *, reason: str | None = None, attempt_id: str | None = None) -> Attempt:
        if self.is_terminal:
            raise InvalidTransitionError("cannot retry a terminal run")
        number = len(self.attempts) + 1
        attempt = Attempt(attempt_id or self.ids.new("attempt"), number, reason)
        self.attempts.append(attempt)
        self.state = replace(self.state, attempt_number=number, status="running")
        self._emit(
            actions={"attempt": {"attempt_id": attempt.attempt_id, "number": number, "reason": reason}},
            metadata={"lifecycle": "attempt_opened", "attempt_id": attempt.attempt_id},
        )
        return attempt

    def admit(self, event: RuntimeEvent) -> RuntimeEvent:
        """Accept a late event only while non-terminal; never mutate history."""

        if self.is_terminal:
            if self._terminal_event and event.id == self._terminal_event.id and event.digest() == self._terminal_event.digest():
                return self._terminal_event
            raise LateEventError(f"run {self.run_id} rejected late event {event.id}")
        persisted = self.emitter.emit(event)
        event = persisted if isinstance(persisted, RuntimeEvent) else event
        self.events.append(event)
        return event

    def mark_uncertain_tool(self, *, call_id: str, reason: str = "outcome not observed") -> RuntimeEvent:
        if self.is_terminal:
            raise LateEventError(f"run {self.run_id} is terminal")
        return self._emit(
            content={"kind": "error", "code": "tool_outcome_uncertain", "message": reason},
            actions={"recovery": {"kind": "tool_outcome_uncertain", "call_id": call_id, "auto_retry": False}},
            refs={"tool_call_id": call_id},
            metadata={"lifecycle": "recovery_visible", "side_effect_retry": "forbidden"},
        )

    def finalize(self, status: str, *, reason: str | None = None) -> RuntimeEvent:
        if status not in TERMINAL_STATES:
            raise InvalidTransitionError(f"{status} is not a terminal state")
        if self._terminal_event is not None:
            if self._terminal_event.status == status and self.state.terminal_reason == reason:
                return self._terminal_event
            raise TerminalConflictError(
                f"run {self.run_id} already finalized as {self._terminal_event.status}"
            )
        event = self._build(
            actions={"run_terminal": {"status": status, "reason": reason}},
            status=status,
            metadata={"lifecycle": "run_terminal"},
        )
        persisted = self.emitter.emit(event)
        event = persisted if isinstance(persisted, RuntimeEvent) else event
        self.events.append(event)
        self._terminal_event = event
        self.state = replace(
            self.state,
            status=status,
            terminal_event_id=event.id,
            terminal_reason=reason,
        )
        return event

    def adopt_terminal_event(self, event: RuntimeEvent) -> None:
        """Register a terminal emitted by a lower-level recorder exactly once."""

        if event.run_id != self.run_id or not event.is_terminal:
            raise LifecycleError("event is not a terminal for this run")
        if self._terminal_event is not None and self._terminal_event.digest() != event.digest():
            raise TerminalConflictError(f"run {self.run_id} has a different terminal")
        self._terminal_event = event
        self.state = replace(
            self.state,
            status=event.status or "failed",
            terminal_event_id=event.id,
            terminal_reason=(event.content or {}).get("message"),
        )

    def child(self, *, run_id: str | None = None, branch: str | None = None) -> "RunStateGuard":
        child_context = self.context.child(
            run_id=run_id or self.ids.new("run"),
            invocation_id=self.ids.new("invocation"),
            branch=branch,
        )
        return RunStateGuard(child_context, self.emitter, id_factory=self.ids)

    def _build(
        self,
        *,
        content: Mapping[str, Any] | None = None,
        actions: Mapping[str, Any] | None = None,
        refs: Mapping[str, Any] | None = None,
        status: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeEvent:
        return RuntimeEvent.create(
            self.context,
            role="system",
            author="system",
            ts=int(datetime.now(timezone.utc).timestamp() * 1000),
            event_id=self.ids.event_id(),
            content=content,
            actions=actions,
            refs=refs,
            status=status,
            metadata=metadata,
        )

    def _emit(self, **kwargs: Any) -> RuntimeEvent:
        event = self._build(**kwargs)
        persisted = self.emitter.emit(event)
        event = persisted if isinstance(persisted, RuntimeEvent) else event
        self.events.append(event)
        return event

    def complete(self, reason: str | None = None) -> RuntimeEvent:
        return self.finalize("completed", reason=reason)

    def fail(self, reason: str | None = None) -> RuntimeEvent:
        return self.finalize("failed", reason=reason)

    def cancel(self, reason: str | None = None) -> RuntimeEvent:
        return self.finalize("cancelled", reason=reason)

    def abort(self, reason: str | None = None) -> RuntimeEvent:
        return self.finalize("aborted", reason=reason)

    def budget_exceeded(self, reason: str | None = None) -> RuntimeEvent:
        return self.finalize("budget_exceeded", reason=reason)


__all__ = [
    "ALL_STATES",
    "Attempt",
    "InvalidTransitionError",
    "LateEventError",
    "LifecycleError",
    "LifecycleState",
    "NON_TERMINAL_STATES",
    "RunStateGuard",
    "TERMINAL_STATES",
    "TerminalConflictError",
]
