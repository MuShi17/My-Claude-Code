"""Deterministic, offline fixtures for the canonical runtime-event changes.

This module intentionally contains no production imports.  It describes logical
runtime scenarios and provides adapters that later changes can feed into the
real RuntimeEvent/Store implementations without needing a network or API key.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


DEFAULT_TIME = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc)
_ID_KEYS = {"id", "event_id", "request_id", "session_id", "turn_id", "run_id", "invocation_id", "call_id"}
_NON_SEMANTIC_KEYS = {
    "timestamp",
    "ts",
    "created_at",
    "provider",
    "model",
    "latency_ms",
    # This digest is derived from fixture arguments and therefore changes when
    # a test intentionally swaps one temporary workspace for another.
    "arguments_digest",
}


class FixedClock:
    """A clock whose output is stable and can be advanced explicitly."""

    def __init__(self, start: datetime = DEFAULT_TIME):
        if start.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._current = start.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._current

    def advance(self, milliseconds: int) -> datetime:
        self._current += timedelta(milliseconds=milliseconds)
        return self._current


class DeterministicIdFactory:
    """Generate predictable IDs while retaining the identity relationships."""

    def __init__(self, prefix: str = "fixture"):
        self.prefix = prefix
        self._counter = 0

    def new(self, kind: str) -> str:
        self._counter += 1
        return f"{self.prefix}-{kind}-{self._counter:04d}"


@dataclass(frozen=True)
class LogicalRunContext:
    session_id: str = "session-fixture-0001"
    turn_id: str = "turn-fixture-0001"
    run_id: str = "run-fixture-0001"
    invocation_id: str = "invocation-fixture-0001"
    parent_run_id: str | None = None


@dataclass(frozen=True)
class PermissionDecision:
    decision: str = "allow"
    reason: str = "fixture policy"


@dataclass(frozen=True)
class ToolCallFixture:
    name: str = "read_file"
    arguments: dict[str, Any] = field(
        default_factory=lambda: {"file_path": "<WORKSPACE>/sample.txt"}
    )
    result: str = "alpha\nbeta\n"
    success: bool = True


@dataclass(frozen=True)
class RuntimeScenario:
    """Logical input shared by provider, store, projection and recovery tests."""

    user_message: str = "Read sample.txt"
    model_text: str = "I will read the file."
    context: LogicalRunContext = field(default_factory=LogicalRunContext)
    permission: PermissionDecision = field(default_factory=PermissionDecision)
    tool: ToolCallFixture = field(default_factory=ToolCallFixture)


def build_scenario(
    *,
    workspace: Path | str = Path("<WORKSPACE>"),
    clock: FixedClock | None = None,
    ids: DeterministicIdFactory | None = None,
    permission: str = "allow",
) -> RuntimeScenario:
    """Build a scenario while allowing time, IDs and workspace to be injected."""

    del clock, ids  # accepted so callers can share one fixture factory with production code
    workspace_text = str(workspace).replace("\\", "/")
    return RuntimeScenario(
        permission=PermissionDecision(decision=permission),
        tool=ToolCallFixture(arguments={"file_path": f"{workspace_text}/sample.txt"}),
    )


def scenario_events(
    scenario: RuntimeScenario,
    *,
    clock: FixedClock | None = None,
    ids: DeterministicIdFactory | None = None,
    provider: str = "fixture",
) -> list[dict[str, Any]]:
    """Render a logical scenario into provider-neutral semantic event dictionaries."""

    clock = clock or FixedClock()
    ids = ids or DeterministicIdFactory()
    ctx = scenario.context
    call_id = ids.new("call")
    event_id = lambda: ids.new("event")
    now = lambda: clock.now().isoformat().replace("+00:00", "Z")

    return [
        {
            "id": event_id(),
            "timestamp": now(),
            "session_id": ctx.session_id,
            "turn_id": ctx.turn_id,
            "run_id": ctx.run_id,
            "invocation_id": ctx.invocation_id,
            "provider": provider,
            "kind": "invocation_opened",
            "partial": False,
        },
        {
            "id": event_id(),
            "timestamp": now(),
            "session_id": ctx.session_id,
            "turn_id": ctx.turn_id,
            "run_id": ctx.run_id,
            "invocation_id": ctx.invocation_id,
            "provider": provider,
            "kind": "text",
            "text": scenario.model_text,
            "partial": False,
        },
        {
            "id": event_id(),
            "timestamp": now(),
            "session_id": ctx.session_id,
            "turn_id": ctx.turn_id,
            "run_id": ctx.run_id,
            "invocation_id": ctx.invocation_id,
            "call_id": call_id,
            "provider": provider,
            "kind": "function_call",
            "name": scenario.tool.name,
            "arguments": scenario.tool.arguments,
            "partial": False,
        },
        {
            "id": event_id(),
            "timestamp": now(),
            "session_id": ctx.session_id,
            "turn_id": ctx.turn_id,
            "run_id": ctx.run_id,
            "invocation_id": ctx.invocation_id,
            "call_id": call_id,
            "provider": provider,
            "kind": "permission",
            "decision": scenario.permission.decision,
            "reason": scenario.permission.reason,
            "partial": False,
        },
        {
            "id": event_id(),
            "timestamp": now(),
            "session_id": ctx.session_id,
            "turn_id": ctx.turn_id,
            "run_id": ctx.run_id,
            "invocation_id": ctx.invocation_id,
            "call_id": call_id,
            "provider": provider,
            "kind": "tool_dispatch",
            "name": scenario.tool.name,
            "arguments_digest": hashlib.sha256(
                json.dumps(scenario.tool.arguments, sort_keys=True).encode()
            ).hexdigest(),
            "partial": False,
        },
        {
            "id": event_id(),
            "timestamp": now(),
            "session_id": ctx.session_id,
            "turn_id": ctx.turn_id,
            "run_id": ctx.run_id,
            "invocation_id": ctx.invocation_id,
            "call_id": call_id,
            "provider": provider,
            "kind": "tool_outcome",
            "name": scenario.tool.name,
            "success": scenario.tool.success,
            "result": scenario.tool.result,
            "partial": False,
        },
        {
            "id": event_id(),
            "timestamp": now(),
            "session_id": ctx.session_id,
            "turn_id": ctx.turn_id,
            "run_id": ctx.run_id,
            "invocation_id": ctx.invocation_id,
            "call_id": call_id,
            "provider": provider,
            "kind": "function_response",
            "result": scenario.tool.result,
            "partial": False,
        },
    ]


@dataclass(frozen=True)
class ProviderChunk:
    kind: str
    payload: dict[str, Any]


class FakeProviderScript:
    """Offline provider script; it never imports or calls provider SDKs."""

    def __init__(self, provider: str, scenario: RuntimeScenario):
        if provider not in {"anthropic", "openai"}:
            raise ValueError(f"unsupported fake provider: {provider}")
        self.provider = provider
        self.scenario = scenario

    def stream(self) -> Iterator[ProviderChunk]:
        for event in scenario_events(self.scenario, provider=self.provider):
            yield ProviderChunk(event["kind"], event)

    def final_response(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "chunks": [chunk.payload for chunk in self.stream()],
            "usage": {"input_tokens": 10, "output_tokens": 8},
            "finish_reason": "tool_use",
        }


class FaultInjector:
    """One-shot deterministic fault hook used by store/recovery tests."""

    def __init__(self, *points: str):
        self._points = set(points)
        self.triggered: list[str] = []

    def check(self, point: str) -> None:
        if point in self._points:
            self.triggered.append(point)
            raise RuntimeError(f"fixture fault at {point}")


@contextmanager
def isolated_home() -> Iterator[Path]:
    """Provide a temporary HOME without touching the user's mini-claude data."""

    with tempfile.TemporaryDirectory(prefix="mini-claude-test-home-") as directory:
        home = Path(directory)
        old_home = os.environ.get("HOME")
        old_userprofile = os.environ.get("USERPROFILE")
        os.environ["HOME"] = str(home)
        os.environ["USERPROFILE"] = str(home)
        try:
            yield home
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_userprofile is None:
                os.environ.pop("USERPROFILE", None)
            else:
                os.environ["USERPROFILE"] = old_userprofile


def _stable_value(value: Any, *, ids: dict[tuple[str, str], str], temp_roots: tuple[str, ...], key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            name: _stable_value(value[name], ids=ids, temp_roots=temp_roots, key=name)
            for name in sorted(value)
            if name not in _NON_SEMANTIC_KEYS
        }
    if isinstance(value, list):
        return [_stable_value(item, ids=ids, temp_roots=temp_roots, key=key) for item in value]
    if isinstance(value, tuple):
        return [_stable_value(item, ids=ids, temp_roots=temp_roots, key=key) for item in value]
    if isinstance(value, str):
        normalised = value.replace("\\", "/")
        for root in temp_roots:
            normalised = normalised.replace(root.replace("\\", "/"), "<TEMP>")
        if key in _ID_KEYS or key.endswith("_id"):
            identity_key = (key, normalised)
            ids.setdefault(identity_key, f"<{key.upper()}.{len(ids) + 1}>")
            return ids[identity_key]
        return normalised
    return value


def stable_projection(value: Any, *, temp_roots: tuple[str, ...] = ()) -> Any:
    """Return stable semantic fields for parity/golden comparisons."""

    return _stable_value(value, ids={}, temp_roots=temp_roots)


def stable_digest(value: Any, *, temp_roots: tuple[str, ...] = ()) -> str:
    encoded = json.dumps(
        stable_projection(value, temp_roots=temp_roots),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_diff(left: Any, right: Any, *, temp_roots: tuple[str, ...] = ()) -> tuple[Any, Any] | None:
    left_stable = stable_projection(left, temp_roots=temp_roots)
    right_stable = stable_projection(right, temp_roots=temp_roots)
    return None if left_stable == right_stable else (left_stable, right_stable)


def assert_no_secrets(value: Any) -> None:
    """Reject common credential patterns in fixture/golden data."""

    serialised = json.dumps(value, ensure_ascii=False).lower()
    assert "sk-ant-" not in serialised
    assert "api_key" not in serialised
    assert "authorization: bearer" not in serialised
    assert not re.search(r"bearer\s+[a-z0-9._-]{12,}", serialised)
