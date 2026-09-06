"""Durable memory context event tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mini_claude.agent import Agent
from mini_claude.event_sink import CanonicalSinkError, RecordingEventSink
from mini_claude.projections import CanonicalModelContextAdapter, ModelReplayProjection, SessionProjection, RunTraceProjection
from mini_claude.runtime_store import SQLiteRuntimeStore
import pytest


def test_memory_context_is_distinct_replayable_and_idempotent(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    memory = SimpleNamespace(
        path=str(tmp_path / "memory.md"),
        content="canonical memory content",
        header="Memory fixture",
    )
    with SQLiteRuntimeStore(database) as store:
        agent = Agent(
            api_base="https://fake-provider.invalid/v1",
            api_key="fixture-key",
            is_sub_agent=True,
            runtime_store=store,
        )
        agent._ask_count = 1
        agent._setup_runtime_facade()
        first = agent._persist_memory_context_event([memory])
        second = agent._persist_memory_context_event([memory])

        assert first.id == second.id
        context_events = [
            event for _, event in store.read_event_records()
            if event.content and event.content.get("kind") == "context"
        ]
        assert len(context_events) == 1
        context_event = context_events[0]
        assert context_event.author == "system"
        assert context_event.content["context_type"] == "memory"
        assert list(context_event.content["sources"]) == [str(tmp_path / "memory.md")]

        replay = ModelReplayProjection().build(store)
        context_message = next(
            message for message in replay.messages
            if message.get("context_type") == "memory"
        )
        assert context_message["content"].endswith("canonical memory content\n</system-reminder>")
        provider = CanonicalModelContextAdapter().build(
            store, provider="openai", system_prompt="system"
        )
        assert all("context_type" not in message for message in provider.messages)
        session = SessionProjection().build(store)
        assert any(message.get("role") == "context" for message in session.messages)
        trace = RunTraceProjection().build(store)
        trace_entry = next(entry for entry in trace.entries if entry.get("kind") == "context")
        assert trace_entry["context_type"] == "memory"

    with SQLiteRuntimeStore(database) as reopened:
        replay = ModelReplayProjection().build(reopened)
        assert sum(message.get("context_type") == "memory" for message in replay.messages) == 1


def test_memory_event_failure_is_visible_and_does_not_append(tmp_path: Path):
    sink = RecordingEventSink()
    agent = Agent(
        api_base="https://fake-provider.invalid/v1",
        api_key="fixture-key",
        is_sub_agent=True,
        runtime_sink=sink,
    )
    agent._ask_count = 1
    agent._setup_runtime_facade()

    def fail(point, event):
        del event
        if point == "emit":
            raise OSError("memory persistence unavailable")

    sink._failure_hook = fail
    memory = SimpleNamespace(
        path=str(tmp_path / "memory.md"), content="retryable", header="Memory"
    )
    with pytest.raises(CanonicalSinkError, match="memory persistence unavailable"):
        agent._persist_memory_context_event([memory])
    assert not any(
        event.content and event.content.get("kind") == "context" for event in sink.events
    )
