"""C07 run/child/attempt/terminal lifecycle contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_claude.event_ids import RunContext
from mini_claude.event_sink import RecordingEventSink, RuntimeEventEmitter
from mini_claude.run_lifecycle import (
    InvalidTransitionError,
    LateEventError,
    RunStateGuard,
    TerminalConflictError,
)
from mini_claude.runtime_event import RuntimeEvent
from mini_claude.runtime_store import SQLiteRuntimeStore


def _guard(sink=None) -> tuple[RunStateGuard, RecordingEventSink]:
    sink = sink or RecordingEventSink()
    emitter = RuntimeEventEmitter(sink)
    return RunStateGuard(RunContext("s-c07", "t-c07", "run-c07", "inv-c07"), emitter), sink


def test_state_machine_rejects_invalid_transitions_and_seals_once():
    guard, sink = _guard()
    with pytest.raises(InvalidTransitionError):
        guard.awaiting_tool()
    guard.start()
    guard.awaiting_tool()
    guard.resume_running()
    terminal = guard.complete("normal")
    assert terminal.status == "completed"
    assert guard.complete("normal") is terminal
    with pytest.raises(TerminalConflictError):
        guard.fail("late failure")
    assert guard.state.status == "completed"
    assert len([event for event in sink.events if event.is_terminal]) == 1


def test_late_event_is_rejected_without_mutating_terminal_history():
    guard, sink = _guard()
    guard.start()
    terminal = guard.cancel("ctrl-c")
    late = RuntimeEvent.create(
        guard.context,
        role="model",
        author="agent",
        content={"kind": "text", "text": "late"},
        event_id="late-event",
        ts=terminal.ts + 1,
    )
    with pytest.raises(LateEventError):
        guard.admit(late)
    assert sink.events[-1] == terminal


def test_child_terminal_does_not_seal_parent_and_attempts_are_retained():
    guard, sink = _guard()
    guard.start()
    first = guard.new_attempt(reason="provider overloaded")
    second = guard.new_attempt(reason="explicit retry")
    assert (first.number, second.number) == (1, 2)
    child = guard.child(branch="explore")
    child.start()
    child_terminal = child.complete("child done")
    assert child_terminal.parent_run_id == guard.run_id
    assert child.is_terminal is True
    assert guard.is_terminal is False
    guard.complete("parent done")
    assert len({event.run_id for event in sink.events}) == 2
    assert {event.run_id for event in sink.events if event.is_terminal} == {
        guard.run_id,
        child.run_id,
    }


def test_sqlite_terminal_finalizer_and_uncertain_tool_evidence_are_reopenable(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        emitter = RuntimeEventEmitter(store)
        guard = RunStateGuard(RunContext("s", "t", "run", "inv"), emitter)
        guard.start()
        guard.mark_uncertain_tool(call_id="call-1")
        terminal = guard.fail("recovery required")
        assert store.run_state("run").terminal_event_id == terminal.id
        assert guard.fail("recovery required") is terminal
    with SQLiteRuntimeStore(database) as reopened:
        events = reopened.read_events(run_id="run")
        assert any(event.actions and "recovery" in event.actions for event in events)
        assert events[-1].status == "failed"


def test_budget_terminal_is_idempotent_after_model_call_finished():
    guard, sink = _guard()
    guard.start()
    terminal = guard.budget_exceeded("turn limit reached")

    assert guard.budget_exceeded("turn limit reached") is terminal
    assert [event.status for event in sink.events if event.is_terminal] == ["budget_exceeded"]
