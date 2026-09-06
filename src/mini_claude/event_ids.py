"""Shared identity primitives for canonical runtime events.

One small identity factory lets provider adapters, tools and child runs all
describe the same session/turn/run/invocation coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass
from secrets import token_hex
from typing import Callable


class IdentityError(ValueError):
    """Raised when a runtime identity is empty or internally inconsistent."""


def _require_identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdentityError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class RunContext:
    """The durable coordinate shared by all events in one invocation lane."""

    session_id: str
    turn_id: str
    run_id: str
    invocation_id: str
    parent_run_id: str | None = None
    branch: str | None = None

    def __post_init__(self) -> None:
        for name in ("session_id", "turn_id", "run_id", "invocation_id"):
            _require_identifier(getattr(self, name), name)
        if self.parent_run_id is not None:
            _require_identifier(self.parent_run_id, "parent_run_id")
            if self.parent_run_id == self.run_id:
                raise IdentityError("parent_run_id cannot equal run_id")
        if self.branch is not None:
            _require_identifier(self.branch, "branch")

    def child(
        self,
        *,
        run_id: str,
        invocation_id: str | None = None,
        turn_id: str | None = None,
        branch: str | None = None,
    ) -> "RunContext":
        """Create an addressable child run without mixing event sequences."""

        return RunContext(
            session_id=self.session_id,
            turn_id=turn_id or self.turn_id,
            run_id=run_id,
            invocation_id=invocation_id or self.invocation_id,
            parent_run_id=self.run_id,
            branch=branch,
        )


class IdentityFactory:
    """Generate namespaced ids while allowing deterministic test injection."""

    def __init__(
        self,
        *,
        token_factory: Callable[[], str] | None = None,
        prefix: str = "rt",
    ) -> None:
        self._token_factory = token_factory or (lambda: token_hex(12))
        self.prefix = prefix

    def new(self, kind: str) -> str:
        _require_identifier(kind, "kind")
        return f"{self.prefix}-{kind}-{self._token_factory()}"

    def event_id(self) -> str:
        return self.new("event")

    def run_id(self) -> str:
        return self.new("run")

    def invocation_id(self) -> str:
        return self.new("invocation")

    def tool_call_id(self) -> str:
        return self.new("tool-call")

    def child_context(
        self,
        parent: RunContext,
        *,
        turn_id: str | None = None,
        branch: str | None = None,
    ) -> RunContext:
        return parent.child(
            run_id=self.run_id(),
            invocation_id=self.invocation_id(),
            turn_id=turn_id,
            branch=branch,
        )


def create_event_id(prefix: str = "rt-event") -> str:
    """Return a best-effort process-independent event id."""

    return f"{prefix}-{token_hex(12)}"


def create_run_context(
    *,
    session_id: str,
    turn_id: str,
    factory: IdentityFactory | None = None,
    run_id: str | None = None,
    invocation_id: str | None = None,
    parent_run_id: str | None = None,
    branch: str | None = None,
) -> RunContext:
    factory = factory or IdentityFactory()
    return RunContext(
        session_id=session_id,
        turn_id=turn_id,
        run_id=run_id or factory.run_id(),
        invocation_id=invocation_id or factory.invocation_id(),
        parent_run_id=parent_run_id,
        branch=branch,
    )


__all__ = [
    "IdentityError",
    "IdentityFactory",
    "RunContext",
    "create_event_id",
    "create_run_context",
]
