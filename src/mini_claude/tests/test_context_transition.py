"""Effective-context transition durability and replay tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_claude.compaction import CompactionCheckpointBuilder
from mini_claude.context_transition import (
    ContextReplacement,
    ContextTransition,
    ContextTransitionError,
    build_context_transition,
)
from mini_claude.event_sink import RecordingEventSink, RuntimeEventEmitter
from mini_claude.event_ids import RunContext
from mini_claude.projections import ModelReplayProjection
from mini_claude.projections.base import EventRecord, source_digest
from mini_claude.runtime_event import RuntimeEvent
from mini_claude.runtime_store import SQLiteRuntimeStore, StoreFaultError

from runtime_fixtures import DeterministicIdFactory, build_scenario, scenario_events


def _events() -> list[RuntimeEvent]:
    return [
        RuntimeEvent.from_dict(item)
        for item in scenario_events(
            build_scenario(), ids=DeterministicIdFactory("transition"), provider="anthropic"
        )
    ]


def _transition_event(
    base: RuntimeEvent, transition: ContextTransition, *, event_id: str = "transition-event"
) -> RuntimeEvent:
    return RuntimeEvent.create(
        RunContext(
            session_id=base.session_id,
            turn_id=base.turn_id,
            run_id=base.run_id,
            invocation_id=base.invocation_id,
        ),
        role="system",
        author="system",
        actions={"context_transition": transition.to_dict()},
        refs={"context_epoch": transition.context_epoch},
        metadata={"lifecycle": "context_transition"},
        event_id=event_id,
    )


def test_transition_rejects_tampered_replacement_digest():
    replacement = ContextReplacement("event-1", "snipped", "stale_snip")
    value = build_context_transition(
        source_high_water=1,
        source_digest="digest",
        projection_version="projection-v1",
        policy_version="compression-policy-v1",
        context_epoch="context:initial",
        reason="stale_snip",
        replacements=[replacement],
        effective_context={"replacements": [replacement.to_dict()]},
    ).to_dict()
    value["replacements"][0]["replacement_digest"] = "tampered"
    with pytest.raises(ContextTransitionError, match="replacement digest mismatch"):
        ContextTransition.from_value(value)


def test_model_replay_applies_durable_replacement_transition():
    events = _events()
    response_event = events[-1]
    replacement = ContextReplacement(
        response_event.id,
        "[Old result cleared]",
        "microcompact",
        target_call_id=str(response_event.content["id"]),
    )
    transition = build_context_transition(
        source_high_water=len(events),
        source_digest=source_digest(
            [EventRecord(i, event) for i, event in enumerate(events, 1)]
        ),
        projection_version="projection-v1",
        policy_version="compression-policy-v1",
        context_epoch="context:initial",
        reason="microcompact",
        replacements=[replacement],
        effective_context={"replacements": [replacement.to_dict()]},
    )
    replay = ModelReplayProjection().build([*events, _transition_event(events[0], transition)])
    tool_message = next(message for message in replay.messages if message.get("role") == "tool")
    assert tool_message["content"] == "[Old result cleared]"
    assert replay.context_epoch == "context:initial"


def test_prepared_long_replacement_keeps_transition_digest_valid():
    events = _events()
    before = ModelReplayProjection().build(events)
    response_event = events[-1]
    long_value = "x" * 9_000
    replacement = ContextReplacement(
        response_event.id,
        long_value,
        "budget_truncation",
        target_call_id=str(response_event.content["id"]),
    )
    transition = build_context_transition(
        source_high_water=len(events),
        source_digest=before.source_digest,
        projection_version=before.projection_version,
        policy_version="compression-policy-v1",
        context_epoch="context:initial",
        reason="budget_truncation",
        replacements=[replacement],
        effective_context={"replacements": [replacement.to_dict()]},
    )
    event = _transition_event(events[0], transition, event_id="long-replacement")
    prepared = RuntimeEventEmitter(RecordingEventSink()).prepare(event)
    persisted = prepared.actions["context_transition"]["replacements"][0]["replacement"]
    assert persisted == long_value
    replay = ModelReplayProjection().build([*events, prepared])
    assert not any(item.code == "invalid_context_transition" for item in replay.diagnostics)
    assert next(message for message in replay.messages if message.get("role") == "tool")["content"] == long_value


def test_prepared_redacted_replacement_rebases_digest_metadata():
    events = _events()
    before = ModelReplayProjection().build(events)
    response_event = events[-1]
    replacement = ContextReplacement(
        response_event.id,
        "sk-ant-transition-secret",
        "secret_scrub",
        target_call_id=str(response_event.content["id"]),
    )
    transition = build_context_transition(
        source_high_water=len(events),
        source_digest=before.source_digest,
        projection_version=before.projection_version,
        policy_version="compression-policy-v1",
        context_epoch="context:initial",
        reason="secret_scrub",
        replacements=[replacement],
        effective_context={"replacements": [replacement.to_dict()]},
    )
    prepared = RuntimeEventEmitter(RecordingEventSink()).prepare(
        _transition_event(events[0], transition, event_id="redacted-replacement")
    )
    item = prepared.actions["context_transition"]["replacements"][0]
    assert item["replacement"] == "[REDACTED]"
    replay = ModelReplayProjection().build([*events, prepared])
    assert not any(item.code == "invalid_context_transition" for item in replay.diagnostics)
    assert "sk-ant-transition-secret" not in str(prepared.to_dict())


def test_prepared_long_compaction_context_keeps_transition_digest_valid():
    events = _events()
    before = ModelReplayProjection().build(events)
    long_value = "context " * 1_500
    context_messages = [{"role": "user", "content": long_value}]
    transition = build_context_transition(
        source_high_water=len(events),
        source_digest=before.source_digest,
        projection_version=before.projection_version,
        policy_version="compression-policy-v1",
        context_epoch="context:long-checkpoint",
        reason="full_compaction",
        replacements=[],
        effective_context=context_messages,
    )
    event = RuntimeEvent.create(
        RunContext(
            session_id=events[0].session_id,
            turn_id=events[0].turn_id,
            run_id=events[0].run_id,
            invocation_id="long-compaction",
        ),
        role="system",
        author="system",
        actions={
            "context_transition": transition.to_dict(),
            "compaction": {
                "reset_model_context": True,
                "context_messages": context_messages,
            },
        },
        metadata={"lifecycle": "compaction_checkpoint"},
    )
    prepared = RuntimeEventEmitter(RecordingEventSink()).prepare(event)
    assert prepared.actions["compaction"]["context_messages"][0]["content"] == long_value
    replay = ModelReplayProjection().build([*events, prepared])
    assert not any(item.code == "invalid_context_transition" for item in replay.diagnostics)
    assert replay.messages[-1]["content"] == long_value


def test_checkpoint_and_transition_commit_or_rollback_together(tmp_path: Path):
    events = _events()
    database = tmp_path / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        for event in events:
            store.append(event)
        checkpoint = CompactionCheckpointBuilder().build(store, high_water=len(events))
        transition = build_context_transition(
            source_high_water=checkpoint.source_high_water,
            source_digest=checkpoint.source_digest,
            projection_version=checkpoint.projection_version,
            policy_version="compression-policy-v1",
            context_epoch="context:" + checkpoint.checkpoint_id,
            reason="full_compaction",
            replacements=[],
            effective_context=[],
        )
        event = _transition_event(events[0], transition)
        store.append_compaction_transition(checkpoint, event)
        assert store.read_compaction_checkpoint(checkpoint.checkpoint_id) is not None
        assert store.current_high_water == len(events) + 1

    class CommitFault:
        def check(self, point: str) -> None:
            if point == "store.commit":
                raise RuntimeError("commit fault")

    with SQLiteRuntimeStore(database) as store:
        before = store.current_high_water
        checkpoint = CompactionCheckpointBuilder().build(
            store, high_water=before, checkpoint_id="failed-checkpoint"
        )
        transition = build_context_transition(
            source_high_water=before,
            source_digest=checkpoint.source_digest,
            projection_version=checkpoint.projection_version,
            policy_version="compression-policy-v1",
            context_epoch="context:failed",
            reason="full_compaction",
            replacements=[],
            effective_context=[],
        )
        event = _transition_event(events[0], transition, event_id="failed-transition-event")
        store.fault_hook = CommitFault()
        with pytest.raises(StoreFaultError, match="commit fault"):
            store.append_compaction_transition(checkpoint, event)
        assert store.current_high_water == before
        assert store.read_compaction_checkpoint(checkpoint.checkpoint_id) is None
