"""Pre-commit transition validation and atomic activation tests."""

from __future__ import annotations

from pathlib import Path
from copy import deepcopy

import pytest

from mini_claude.agent import Agent
from mini_claude.compaction import CompactionCheckpointBuilder, CompactionError
from mini_claude.context_transition import (
    ContextReplacement,
    ContextTransitionError,
    build_context_transition,
    validate_transition_candidate,
)
from mini_claude.event_ids import RunContext
from mini_claude.projections import ModelReplayProjection
from mini_claude.projections.base import EventRecord, source_digest
from mini_claude.runtime_event import RuntimeEvent
from mini_claude.runtime_store import SQLiteRuntimeStore, StoreFaultError, StoreValidationError

from runtime_fixtures import DeterministicIdFactory, build_scenario, scenario_events


def _events(*, context_id: str | None = None) -> list[RuntimeEvent]:
    values = scenario_events(
        build_scenario(), ids=DeterministicIdFactory("activation"), provider="anthropic"
    )
    if context_id is not None:
        values = [
            {**value, "context_id": context_id}
            for value in values
        ]
    return [RuntimeEvent.from_dict(value) for value in values]


def _transition_event(
    base: RuntimeEvent,
    transition,
    *,
    event_id: str,
    compaction: dict | None = None,
) -> RuntimeEvent:
    actions = {"context_transition": transition.to_dict()}
    if compaction is not None:
        actions["compaction"] = compaction
    context = RunContext(
        session_id=base.session_id,
        turn_id=base.turn_id,
        run_id=base.run_id,
        invocation_id=base.invocation_id,
        context_id=base.context_id,
    )
    return RuntimeEvent.create(
        context,
        role="system",
        author="system",
        actions=actions,
        metadata={"lifecycle": "context_transition"},
        event_id=event_id,
    )


def test_candidate_validation_uses_exact_event_identity_with_repeated_call_ids():
    first = _events()
    second_values = []
    for index, event in enumerate(first, start=1):
        value = event.to_dict()
        value.update(
            {
                "id": f"activation-second-{index}",
                "run_id": "run-second",
                "turn_id": "turn-second",
                "invocation_id": "invocation-second",
            }
        )
        second_values.append(value)
    second = [RuntimeEvent.from_dict(value) for value in second_values]
    all_events = [*first, *second]
    projection = ModelReplayProjection().build(all_events)
    target = second[-1]
    replacement = ContextReplacement(
        target_event_id=target.id,
        target_call_id=str(target.content["id"]),
        replacement="second run compressed",
        reason="microcompact",
    )
    transition = build_context_transition(
        source_high_water=len(all_events),
        source_digest=projection.source_digest,
        projection_version=projection.projection_version,
        policy_version="compression-policy-v1",
        context_epoch="context:initial",
        reason="microcompact",
        replacements=[replacement],
        effective_context={"replacements": [replacement.to_dict()]},
    )

    result = validate_transition_candidate(
        projection.messages,
        transition,
        source_high_water=len(all_events),
        source_digest=projection.source_digest,
        expected_projection_version=projection.projection_version,
        expected_policy_version="compression-policy-v1",
        current_context_epoch="context:initial",
    )
    tool_messages = [message for message in result.messages if message.get("role") == "tool"]
    assert [message["content"] for message in tool_messages] == [
        first[-1].content["result"],
        "second run compressed",
    ]


def test_candidate_validation_rejects_missing_and_ambiguous_targets():
    messages = [
        {"role": "user", "content": "hello", "runtime_event_id": "user-1"},
    ]
    replacement = ContextReplacement("missing", "new", "microcompact")
    transition = build_context_transition(
        source_high_water=2,
        source_digest="source",
        projection_version="projection-v1",
        policy_version="compression-policy-v1",
        context_epoch="context:initial",
        reason="microcompact",
        replacements=[replacement],
        effective_context={"replacements": [replacement.to_dict()]},
    )
    with pytest.raises(ContextTransitionError, match="target not found"):
        validate_transition_candidate(
            messages,
            transition,
            source_high_water=2,
            source_digest="source",
        )

    ambiguous = [
        {"role": "user", "content": "one", "runtime_event_id": "duplicate"},
        {"role": "user", "content": "two", "runtime_event_id": "duplicate"},
    ]
    replacement = ContextReplacement("duplicate", "new", "microcompact")
    transition = build_context_transition(
        source_high_water=2,
        source_digest="source",
        projection_version="projection-v1",
        policy_version="compression-policy-v1",
        context_epoch="context:initial",
        reason="microcompact",
        replacements=[replacement],
        effective_context={"replacements": [replacement.to_dict()]},
    )
    with pytest.raises(ContextTransitionError, match="ambiguous"):
        validate_transition_candidate(
            ambiguous,
            transition,
            source_high_water=2,
            source_digest="source",
        )


def test_candidate_validation_rejects_incomplete_reset_groups():
    reset = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "call-1", "name": "read_file", "arguments": {}}],
        }
    ]
    transition = build_context_transition(
        source_high_water=1,
        source_digest="source",
        projection_version="projection-v1",
        policy_version="compression-policy-v1",
        context_epoch="context:new",
        reason="full_compaction",
        replacements=[],
        effective_context=reset,
    )
    with pytest.raises(ContextTransitionError, match="no corresponding results"):
        validate_transition_candidate(
            [],
            transition,
            source_high_water=1,
            source_digest="source",
            reset_context=reset,
        )


def test_sqlite_rejects_stale_lightweight_transition_without_append(tmp_path: Path):
    events = _events(context_id="context:activation")
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        for event in events:
            store.append(event)
        source_high_water = store.current_high_water
        projection = ModelReplayProjection().build(
            store, high_water=source_high_water, context_id="context:activation"
        )
        target = events[-1]
        replacement = ContextReplacement(
            target_event_id=target.id,
            target_call_id=str(target.content["id"]),
            replacement="compressed",
            reason="microcompact",
        )
        transition = build_context_transition(
            source_high_water=source_high_water,
            source_digest=projection.source_digest,
            projection_version=projection.projection_version,
            policy_version="compression-policy-v1",
            context_epoch="context:initial",
            reason="microcompact",
            replacements=[replacement],
            effective_context={"replacements": [replacement.to_dict()]},
            context_id="context:activation",
        )
        activation = _transition_event(events[0], transition, event_id="stale-transition")
        store.append(
            RuntimeEvent.create(
                RunContext(
                    session_id=events[0].session_id,
                    turn_id=events[0].turn_id,
                    run_id=events[0].run_id,
                    invocation_id=events[0].invocation_id,
                    context_id="context:activation",
                ),
                role="system",
                author="system",
                actions={"probe": True},
            )
        )
        before = store.current_high_water
        with pytest.raises(StoreValidationError, match="source changed"):
            store.append_context_transition(
                activation,
                source_high_water=source_high_water,
                source_digest=projection.source_digest,
                context_id="context:activation",
            )
        assert store.current_high_water == before
        assert store.read_event("stale-transition") is None


def test_sqlite_lightweight_activation_rolls_back_on_commit_failure(tmp_path: Path):
    events = _events(context_id="context:activation")

    class CommitFault:
        def check(self, point: str) -> None:
            if point == "store.commit":
                raise RuntimeError("activation commit fault")

    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        for event in events:
            store.append(event)
        high_water = store.current_high_water
        projection = ModelReplayProjection().build(
            store, high_water=high_water, context_id="context:activation"
        )
        target = events[-1]
        replacement = ContextReplacement(
            target_event_id=target.id,
            target_call_id=str(target.content["id"]),
            replacement="compressed",
            reason="microcompact",
        )
        transition = build_context_transition(
            source_high_water=high_water,
            source_digest=projection.source_digest,
            projection_version=projection.projection_version,
            policy_version="compression-policy-v1",
            context_epoch="context:initial",
            reason="microcompact",
            replacements=[replacement],
            effective_context={"replacements": [replacement.to_dict()]},
            context_id="context:activation",
        )
        activation = _transition_event(events[0], transition, event_id="failed-transition")
        store.fault_hook = CommitFault()
        with pytest.raises(StoreFaultError, match="activation commit fault"):
            store.append_context_transition(
                activation,
                source_high_water=high_water,
                source_digest=projection.source_digest,
                context_id="context:activation",
            )
        assert store.current_high_water == high_water
        assert store.read_event("failed-transition") is None


def test_sqlite_full_activation_rejects_source_conflict_without_checkpoint(tmp_path: Path):
    events = _events(context_id="context:activation")
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        for event in events:
            store.append(event)
        high_water = store.current_high_water
        checkpoint = CompactionCheckpointBuilder().build(
            store, high_water=high_water, context_id="context:activation"
        )
        transition = build_context_transition(
            source_high_water=high_water,
            source_digest=checkpoint.source_digest,
            projection_version=checkpoint.projection_version,
            policy_version="compression-policy-v1",
            context_epoch="context:compacted",
            reason="full_compaction",
            replacements=[],
            effective_context=[{"role": "user", "content": "summary"}],
            context_id="context:activation",
        )
        activation = _transition_event(
            events[0],
            transition,
            event_id="stale-full-transition",
            compaction={
                "checkpoint_id": checkpoint.checkpoint_id,
                "source_high_water": high_water,
                "source_digest": checkpoint.source_digest,
                "reset_model_context": True,
                "context_messages": [{"role": "user", "content": "summary"}],
            },
        )
        store.append(
            RuntimeEvent.create(
                RunContext(
                    session_id=events[0].session_id,
                    turn_id=events[0].turn_id,
                    run_id=events[0].run_id,
                    invocation_id=events[0].invocation_id,
                    context_id="context:activation",
                ),
                role="system",
                author="system",
                actions={"probe": True},
            )
        )
        with pytest.raises(StoreValidationError, match="source changed"):
            store.append_compaction_transition(checkpoint, activation)
        assert store.read_compaction_checkpoint(checkpoint.checkpoint_id) is None
        assert store.read_event("stale-full-transition") is None


def test_invalid_committed_reset_is_reported_without_clearing_context():
    events = _events()
    projection = ModelReplayProjection().build(events)
    transition = build_context_transition(
        source_high_water=len(events),
        source_digest=projection.source_digest,
        projection_version=projection.projection_version,
        policy_version="compression-policy-v1",
        context_epoch="context:corrupt",
        reason="full_compaction",
        replacements=[],
        effective_context=[],
    )
    corrupt = _transition_event(
        events[0],
        transition,
        event_id="corrupt-reset",
        compaction={
            "reset_model_context": True,
            "context_messages": [{"role": "user", "content": "bad reset"}],
        },
    )
    replay = ModelReplayProjection().build([*events, corrupt])
    assert any(item.code == "invalid_context_transition" for item in replay.diagnostics)
    assert all(message.get("content") != "bad reset" for message in replay.messages)


def test_agent_restores_previous_provider_context_when_transition_source_races(
    tmp_path: Path,
):
    class RaceFault:
        def __init__(self, store: SQLiteRuntimeStore):
            self.store = store
            self.injected = False
            self.probe_id: str | None = None

        def check(self, point: str) -> None:
            if point != "store.append" or self.injected:
                return
            self.injected = True
            self.store.fault_hook = None
            context = agent._runtime_context
            assert context is not None
            probe = RuntimeEvent.create(
                context,
                role="system",
                author="system",
                actions={"probe": True},
            )
            self.probe_id = probe.id
            self.store.append(probe)

    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        agent = Agent(
            api_base="https://fake-provider.invalid/v1",
            api_key="fixture-key",
            is_sub_agent=True,
            runtime_store=store,
            runtime_session_id="session-race",
        )
        agent._ask_count = 1
        agent._setup_runtime_facade()
        context = agent._runtime_context
        assert context is not None
        store.append(
            RuntimeEvent.create(
                context,
                role="model",
                author="agent",
                content={
                    "kind": "function_call",
                    "id": "race-call",
                    "name": "read_file",
                    "args": {},
                },
            )
        )
        store.append(
            RuntimeEvent.create(
                context,
                role="tool",
                author="tool",
                content={
                    "kind": "function_response",
                    "id": "race-call",
                    "name": "read_file",
                    "result": "canonical result",
                },
            )
        )
        agent._refresh_provider_context_from_canonical()
        agent._openai_messages[-1]["content"] = "x" * 20_000
        before = deepcopy(agent._openai_messages)
        agent.last_input_token_count = agent.effective_window
        race = RaceFault(store)
        store.fault_hook = race

        with pytest.raises(CompactionError, match="context transition failed"):
            agent._run_compression_pipeline()
        assert agent._openai_messages == before
        assert race.probe_id is not None
        assert store.read_event(race.probe_id) is not None
        assert not any(
            event.actions and "context_transition" in event.actions
            for _, event in store.read_event_records()
        )


def test_full_then_lightweight_transition_replays_after_restart(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    context_id = "context:chain"
    events = _events(context_id=context_id)
    with SQLiteRuntimeStore(database) as store:
        for event in events:
            store.append(event)
        before = ModelReplayProjection().build(
            store, high_water=store.current_high_water, context_id=context_id
        )
        retained = [dict(message) for message in before.messages[-2:]]
        context_messages = [
            {"role": "user", "content": "durable summary"},
            {"role": "assistant", "content": "continue"},
            *retained,
        ]
        checkpoint = CompactionCheckpointBuilder().build(
            store, high_water=store.current_high_water, context_id=context_id
        )
        full = build_context_transition(
            source_high_water=checkpoint.source_high_water,
            source_digest=checkpoint.source_digest,
            projection_version=checkpoint.projection_version,
            policy_version="compression-policy-v1",
            context_epoch="context:chain-full",
            reason="full_compaction",
            replacements=[],
            effective_context=context_messages,
            context_id=context_id,
        )
        full_event = _transition_event(
            events[0],
            full,
            event_id="chain-full-transition",
            compaction={
                "checkpoint_id": checkpoint.checkpoint_id,
                "source_high_water": checkpoint.source_high_water,
                "source_digest": checkpoint.source_digest,
                "reset_model_context": True,
                "context_messages": context_messages,
            },
        )
        store.append_compaction_transition(checkpoint, full_event)
        compacted = ModelReplayProjection().build(
            store, high_water=store.current_high_water, context_id=context_id
        )
        target = retained[-1]
        replacement = ContextReplacement(
            target_event_id=target["runtime_event_id"],
            target_call_id=target["tool_call_id"],
            replacement="restart-safe bounded result",
            reason="microcompact",
        )
        lightweight = build_context_transition(
            source_high_water=store.current_high_water,
            source_digest=compacted.source_digest,
            projection_version=compacted.projection_version,
            policy_version="compression-policy-v1",
            context_epoch=compacted.context_epoch,
            reason="microcompact",
            replacements=[replacement],
            effective_context={"replacements": [replacement.to_dict()]},
            context_id=context_id,
        )
        lightweight_event = _transition_event(
            events[0], lightweight, event_id="chain-lightweight-transition"
        )
        validate_transition_candidate(
            compacted.messages,
            lightweight,
            source_high_water=compacted.high_water,
            source_digest=compacted.source_digest,
            current_context_epoch=compacted.context_epoch,
            context_id=context_id,
        )
        store.append_context_transition(
            lightweight_event,
            source_high_water=compacted.high_water,
            source_digest=compacted.source_digest,
            context_id=context_id,
        )

    with SQLiteRuntimeStore(database) as reopened:
        replay = ModelReplayProjection().build(
            reopened, context_id=context_id
        )
        assert replay.messages[-1]["content"] == "restart-safe bounded result"
        assert replay.context_epoch == "context:chain-full"
        assert not any(
            item.code == "invalid_context_transition" for item in replay.diagnostics
        )
