"""Warm replay parity and suffix-read tests."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from mini_claude.agent import Agent
from mini_claude.context_transition import ContextReplacement, build_context_transition
from mini_claude.event_ids import RunContext
from mini_claude.projections.base import EventRecord
from mini_claude.projections.incremental_replay import IncrementalModelReplayCursor
from mini_claude.projections.model_replay_projection import ModelReplayProjection
from mini_claude.projections.provider_context import CanonicalModelContextAdapter
from mini_claude.runtime_event import RuntimeEvent
from mini_claude.runtime_store import SQLiteRuntimeStore

from runtime_fixtures import DeterministicIdFactory, build_scenario, scenario_events


def _events(provider: str = "anthropic") -> list[RuntimeEvent]:
    return [
        RuntimeEvent.from_dict(item)
        for item in scenario_events(
            build_scenario(), ids=DeterministicIdFactory("fixture"), provider=provider
        )
    ]


def _multi_tool_events() -> list[RuntimeEvent]:
    events = _events()
    thinking = deepcopy(events[1].to_dict())
    thinking["id"] = "fixture-event-thinking-0001"
    thinking["content"] = {
        "kind": "thinking",
        "text": "signed reasoning",
        "signature": "signature-1",
    }
    second_call = deepcopy(events[2].to_dict())
    second_call["id"] = "fixture-event-call-0002"
    second_call["content"]["id"] = "fixture-call-0002"
    second_call["content"]["args"] = {"file_path": "<WORKSPACE>/other.txt"}
    second_call["refs"] = {"tool_call_id": "fixture-call-0002"}

    second_response = deepcopy(events[-1].to_dict())
    second_response["id"] = "fixture-event-response-0002"
    second_response["content"]["id"] = "fixture-call-0002"
    second_response["content"]["result"] = "gamma\ndelta\n"
    second_response["refs"] = {
        "tool_call_id": "fixture-call-0002",
        "operation_id": "operation-fixture-call-0002",
    }
    return [
        events[0],
        events[1],
        RuntimeEvent.from_dict(thinking),
        events[2],
        RuntimeEvent.from_dict(second_call),
        *events[3:],
        RuntimeEvent.from_dict(second_response),
    ]


def _numbered(events: list[RuntimeEvent]) -> list[EventRecord]:
    return [EventRecord(index, event) for index, event in enumerate(events, start=1)]


def test_warm_cursor_matches_cold_replay_for_multi_tool_and_thinking_shapes():
    events = _multi_tool_events()
    cold = ModelReplayProjection().build(events)
    cursor = IncrementalModelReplayCursor()
    records = _numbered(events)
    cursor.append(records[:4])
    assert not any(message.get("tool_calls") for message in cursor.result().messages)
    cursor = IncrementalModelReplayCursor()
    cursor.append(records[:4])
    cursor.append(records[4:])
    warm = cursor.result()

    assert warm.to_dict() == cold.to_dict()
    assert CanonicalModelContextAdapter().build_result(
        warm, provider="anthropic"
    ).messages == CanonicalModelContextAdapter().build(
        events, provider="anthropic"
    ).messages
    assert CanonicalModelContextAdapter().build_result(
        warm, provider="openai", system_prompt="system"
    ).messages == CanonicalModelContextAdapter().build(
        events, provider="openai", system_prompt="system"
    ).messages
    snapshot = cursor.state_snapshot()
    assert json.loads(json.dumps(snapshot, ensure_ascii=False)) == snapshot
    assert snapshot["source_high_water"] == len(events)


def test_inverse_parallel_completion_keeps_model_call_order_and_matches_cold_replay():
    base = _multi_tool_events()
    finish = RuntimeEvent.create(
        RunContext(
            session_id=base[0].session_id,
            turn_id=base[0].turn_id,
            run_id=base[0].run_id,
            invocation_id=base[0].invocation_id,
        ),
        role="system",
        author="agent",
        actions={"model_finish": {"finish_reason": "tool_use"}},
        metadata={"lifecycle": "model_final"},
    )
    # The model emitted call A then B; the executor persisted B's result first.
    events = [*base[:5], finish, *base[5:8], base[9], base[8]]
    records = _numbered(events)
    cursor = IncrementalModelReplayCursor()
    cursor.append(records[:10])
    assert not any(message.get("tool_calls") for message in cursor.result().messages)
    cursor.append(records[10:])

    cold = ModelReplayProjection().build(events)
    warm = cursor.result()
    assert warm.to_dict() == cold.to_dict()
    call_message = next(message for message in warm.messages if message.get("tool_calls"))
    assert [item["id"] for item in call_message["tool_calls"]] == [
        "fixture-call-0001",
        "fixture-call-0002",
    ]
    assert [
        message["tool_call_id"]
        for message in warm.messages
        if message.get("role") == "tool"
    ] == ["fixture-call-0002", "fixture-call-0001"]


def test_warm_cursor_applies_a_committed_context_transition():
    events = _events()
    records = _numbered(events)
    cold_before = ModelReplayProjection().build(events)
    target = events[-1]
    replacement = ContextReplacement(
        target_event_id=target.id,
        target_call_id=str(target.content["id"]),
        replacement="[snipped tool output]",
        reason="lightweight_compression",
    )
    transition = build_context_transition(
        source_high_water=len(records),
        source_digest=cold_before.source_digest,
        projection_version=cold_before.projection_version,
        policy_version="compression-policy-v1",
        context_epoch="context:initial",
        reason="lightweight_compression",
        replacements=[replacement],
        effective_context={"replacements": [replacement.to_dict()]},
    )
    transition_event = RuntimeEvent.create(
        RunContext(
            session_id=events[0].session_id,
            turn_id=events[0].turn_id,
            run_id=events[0].run_id,
            invocation_id="transition-invocation",
        ),
        role="system",
        author="system",
        actions={"context_transition": transition.to_dict()},
        metadata={"lifecycle": "context_transition"},
    )
    all_events = [*events, transition_event]
    cold = ModelReplayProjection().build(all_events)
    cursor = IncrementalModelReplayCursor()
    cursor.append(records)
    cursor.append([EventRecord(len(all_events), transition_event)])

    assert cursor.result().to_dict() == cold.to_dict()
    assert cursor.result().messages[-1]["content"] == "[snipped tool output]"


def test_warm_cursor_restarts_effective_prefix_at_full_compaction():
    events = _events()
    records = _numbered(events)
    before = ModelReplayProjection().build(events)
    context_messages = [
        {"role": "user", "content": "[Previous conversation summary]"},
        {"role": "assistant", "content": "Continue from the summary."},
    ]
    transition = build_context_transition(
        source_high_water=len(records),
        source_digest=before.source_digest,
        projection_version=before.projection_version,
        policy_version="compression-policy-v1",
        context_epoch="context:checkpoint-1",
        reason="full_compaction",
        replacements=[],
        effective_context=context_messages,
    )
    event = RuntimeEvent.create(
        RunContext(
            session_id=events[0].session_id,
            turn_id=events[0].turn_id,
            run_id=events[0].run_id,
            invocation_id="compaction-invocation",
        ),
        role="system",
        author="system",
        actions={
            "context_transition": transition.to_dict(),
            "compaction": {
                "reset_model_context": True,
                "context_messages": context_messages,
                "context_epoch": "context:checkpoint-1",
            },
        },
        metadata={"lifecycle": "compaction_checkpoint"},
    )
    cold = ModelReplayProjection().build([*events, event])
    cursor = IncrementalModelReplayCursor()
    cursor.append(records)
    cursor.append([EventRecord(len(records) + 1, event)])

    assert cursor.result().to_dict() == cold.to_dict()
    assert cursor.result().context_epoch == "context:checkpoint-1"


def test_full_compaction_preserves_retained_source_ids_for_later_replacement():
    events = _events()
    before = ModelReplayProjection().build(events)
    retained = [dict(message) for message in before.messages[-2:]]
    retained_response_id = retained[-1]["runtime_event_id"]
    context_messages = [
        {"role": "user", "content": "summary"},
        {"role": "assistant", "content": "continue"},
        *retained,
    ]
    compaction_transition = build_context_transition(
        source_high_water=len(events),
        source_digest=before.source_digest,
        projection_version=before.projection_version,
        policy_version="compression-policy-v1",
        context_epoch="context:checkpoint-source-preserving",
        reason="full_compaction",
        replacements=[],
        effective_context=context_messages,
    )
    compaction_event = RuntimeEvent.create(
        RunContext(
            session_id=events[0].session_id,
            turn_id=events[0].turn_id,
            run_id=events[0].run_id,
            invocation_id="compaction-source-preserving",
        ),
        role="system",
        author="system",
        actions={
            "context_transition": compaction_transition.to_dict(),
            "compaction": {
                "reset_model_context": True,
                "context_messages": context_messages,
                "context_epoch": "context:checkpoint-source-preserving",
            },
        },
        metadata={"lifecycle": "compaction_checkpoint"},
    )
    after_compaction = [*events, compaction_event]
    compacted = ModelReplayProjection().build(after_compaction)
    assert compacted.messages[-1]["runtime_event_id"] == retained_response_id

    replacement = ContextReplacement(
        target_event_id=retained_response_id,
        target_call_id=retained[-1]["tool_call_id"],
        replacement="later bounded result",
        reason="microcompact",
    )
    lightweight = build_context_transition(
        source_high_water=len(after_compaction),
        source_digest=ModelReplayProjection().build(after_compaction).source_digest,
        projection_version=compacted.projection_version,
        policy_version="compression-policy-v1",
        context_epoch=compacted.context_epoch,
        reason="lightweight_compression",
        replacements=[replacement],
        effective_context={"replacements": [replacement.to_dict()]},
    )
    lightweight_event = RuntimeEvent.create(
        RunContext(
            session_id=events[0].session_id,
            turn_id=events[0].turn_id,
            run_id=events[0].run_id,
            invocation_id="lightweight-source-preserving",
        ),
        role="system",
        author="system",
        actions={"context_transition": lightweight.to_dict()},
        metadata={"lifecycle": "context_transition"},
    )
    final = ModelReplayProjection().build([*after_compaction, lightweight_event])
    assert final.messages[-1]["content"] == "later bounded result"


class _CountingStore(SQLiteRuntimeStore):
    def __init__(self, path: Path):
        super().__init__(path)
        self.read_after: list[int | None] = []
        self.full_validation_count = 0

    def read_event_records(self, **kwargs):
        self.read_after.append(kwargs.get("after_ordinal"))
        return super().read_event_records(**kwargs)

    def _validate_event_sequences(self, connection):
        self.full_validation_count += 1
        return super()._validate_event_sequences(connection)


def test_suffix_read_does_not_repeat_full_ledger_validation(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    with _CountingStore(database) as store:
        for event in _events():
            store.append(event)
        store.read_event_records()
        assert store.full_validation_count == 1
        store.read_event_records(after_ordinal=1)
        assert store.full_validation_count == 1


def test_agent_warm_refresh_reads_only_the_new_suffix(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    with _CountingStore(database) as store:
        for event in _events():
            store.append(event)
        agent = Agent(
            api_base="https://fake-provider.invalid/v1",
            api_key="fixture-key",
            is_sub_agent=True,
            runtime_store=store,
        )
        agent._ask_count = 1
        agent._setup_runtime_facade()
        agent._refresh_provider_context_from_canonical()
        first_high_water = agent.replay_diagnostics()["source_high_water"]
        agent._refresh_provider_context_from_canonical()
        assert agent.replay_diagnostics()["events_read_last_refresh"] == 0

        suffix_context = RunContext(
            session_id=agent.session_id,
            turn_id="suffix-turn",
            run_id="suffix-run",
            invocation_id="suffix-invocation",
            context_id=agent._runtime_context.context_id,
        )
        opening = RuntimeEvent.create(
            suffix_context,
            role="system",
            author="agent",
            content={
                "kind": "invocation_opened",
                "protocol": "invocation_opened_v1",
                "route": {"provider": "fixture"},
                "configuration": {"lifecycle": "run"},
                "root": {"kind": "agent"},
                "source": {"kind": "fresh"},
            },
            metadata={"lifecycle": "invocation_opened"},
        )
        store.append(opening)
        for index in range(1, 4):
            store.append(
                RuntimeEvent.create(
                    suffix_context,
                    role="user",
                    author="user",
                    content={"kind": "text", "text": f"suffix-{index}"},
                )
            )
        agent._refresh_provider_context_from_canonical()

        assert store.read_after[0] is None
        assert store.read_after[-1] == first_high_water
        assert agent.replay_diagnostics()["mode"] == "warm"
        assert agent.replay_diagnostics()["events_read_last_refresh"] == 4


def test_transition_target_uses_event_scope_when_call_ids_repeat_across_runs():
    first = _events()
    second_dicts = []
    for index, event in enumerate(first, start=1):
        value = event.to_dict()
        value["id"] = f"second-{index:04d}"
        value["turn_id"] = "turn-2"
        value["run_id"] = "run-2"
        value["invocation_id"] = "invocation-2"
        if (value.get("metadata") or {}).get("lifecycle") == "function_response":
            value["content"]["result"] = "second run result"
        second_dicts.append(value)
    second = [RuntimeEvent.from_dict(value) for value in second_dicts]
    combined = [*first, *second]
    records = _numbered(combined)
    before = ModelReplayProjection().build(combined)
    target = second[-1]
    replacement = ContextReplacement(
        target_event_id=target.id,
        target_call_id=str(target.content["id"]),
        replacement="second run compressed",
        reason="microcompact",
    )
    transition = build_context_transition(
        source_high_water=len(records),
        source_digest=before.source_digest,
        projection_version=before.projection_version,
        policy_version="compression-policy-v1",
        context_epoch="context:initial",
        reason="microcompact",
        replacements=[replacement],
        effective_context={"replacements": [replacement.to_dict()]},
    )
    transition_event = RuntimeEvent.create(
        RunContext(
            session_id=first[0].session_id,
            turn_id="turn-2",
            run_id="run-2",
            invocation_id="transition-2",
        ),
        role="system",
        author="system",
        actions={"context_transition": transition.to_dict()},
        metadata={"lifecycle": "context_transition"},
    )
    all_events = [*combined, transition_event]
    cold = ModelReplayProjection().build(all_events)
    cursor = IncrementalModelReplayCursor()
    cursor.append(records)
    cursor.append([EventRecord(len(all_events), transition_event)])
    warm = cursor.result()

    tools = [message for message in warm.messages if message.get("role") == "tool"]
    assert [message["content"] for message in tools] == [
        first[-1].content["result"],
        "second run compressed",
    ]
    assert warm.to_dict() == cold.to_dict()
    assert not any(
        item.code == "invalid_context_transition"
        for item in warm.diagnostics
    )

    agent = Agent(api_key="fixture-key", is_sub_agent=True)
    agent._replay_cursor = cursor
    agent._anthropic_messages = [
        dict(message)
        for message in CanonicalModelContextAdapter().build(
            combined, provider="anthropic"
        ).messages
    ]
    captured = agent._capture_compression_tool_results()
    assert captured[first[-1].id][1] == first[-1].content["result"]
    assert captured[second[-1].id][1] == "second run result"
