"""C08 canonical-first projection/replay tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from mini_claude.projections import (
    CanonicalMetricsProjection,
    CanonicalModelContextAdapter,
    ModelReplayProjection,
    RunTraceProjection,
    SessionProjection,
)
from mini_claude.runtime_event import RuntimeEvent
from mini_claude.runtime_store import SQLiteRuntimeStore
from mini_claude.session import load_session

from runtime_fixtures import DeterministicIdFactory, build_scenario, scenario_events, stable_diff


def _events(provider: str = "fixture") -> list[RuntimeEvent]:
    return [
        RuntimeEvent.from_dict(item)
        for item in scenario_events(
            build_scenario(), ids=DeterministicIdFactory("fixture"), provider=provider
        )
    ]


def test_rebuilding_same_prefix_is_stable_and_high_water_excludes_later_events():
    events = _events()
    session = SessionProjection()
    first = session.build(events[:3], high_water=3)
    second = session.build(events[:3], high_water=3)
    assert first.digest == second.digest
    assert first.high_water == 3
    later = session.build(events, high_water=3)
    assert later.digest == first.digest


def test_model_replay_pairs_tool_call_result_and_ignores_partial_hidden():
    events = _events()
    partial_data = events[2].to_dict()
    partial_data["id"] = "partial-call"
    partial_data["partial"] = True
    hidden_data = events[1].to_dict()
    hidden_data["id"] = "hidden-text"
    hidden_data["model_visibility"] = "hidden"
    replay = ModelReplayProjection().build(
        events + [RuntimeEvent.from_dict(partial_data), RuntimeEvent.from_dict(hidden_data)]
    )
    assert any(message["role"] == "assistant" and "tool_calls" in message for message in replay.messages)
    assert any(message["role"] == "tool" for message in replay.messages)
    assert not any(message.get("runtime_event_id") == "hidden-text" for message in replay.messages)
    assert replay.partial_count == 1


def test_unmatched_call_and_result_are_diagnostic_not_fabricated():
    events = _events()
    unmatched = ModelReplayProjection().build(events[:3])
    assert any(item.code == "unmatched_tool_call" for item in unmatched.diagnostics)
    result_only = ModelReplayProjection().build([events[-1]])
    assert any(item.code == "unmatched_tool_result" for item in result_only.diagnostics)
    assert not any(message.get("role") == "tool" for message in result_only.messages)


def test_session_and_trace_keep_child_identity_and_trace_is_read_only(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    events = _events()
    with SQLiteRuntimeStore(database) as store:
        for event in events:
            store.append(event)
        before = store.current_high_water
        before_digest = store.read_immutable_prefix().digest
        trace = RunTraceProjection().build(store)
        session = SessionProjection().build(store)
        assert trace.high_water == before
        assert session.runs[0]["run_id"] == events[0].run_id
        assert store.current_high_water == before
        assert store.read_immutable_prefix().digest == before_digest
        assert all("ordinal" in entry for entry in trace.entries)


def test_provider_parity_uses_same_stable_projection_fields():
    anthropic = ModelReplayProjection().build(_events("anthropic"))
    openai = ModelReplayProjection().build(_events("openai"))
    assert anthropic.messages == openai.messages
    assert anthropic.digest == openai.digest


def test_replay_rejects_response_before_call_and_adapters_use_provider_shapes():
    events = _events()
    invalid_order = [events[0], events[6], events[2]]
    replay = ModelReplayProjection().build(invalid_order)
    assert not any(message.get("role") == "tool" for message in replay.messages)
    assert any(item.code == "invalid_tool_order" for item in replay.diagnostics)

    thinking = events[1].to_dict()
    thinking.update(
        {
            "id": "signed-thinking",
            "content": {"kind": "thinking", "text": "reason", "signature": "sig-1"},
            "role": "model",
        }
    )
    replay = ModelReplayProjection().build([events[0], RuntimeEvent.from_dict(thinking), events[1]])
    anthropic = CanonicalModelContextAdapter().build([events[0], RuntimeEvent.from_dict(thinking)], provider="anthropic")
    assert anthropic.messages[0]["content"][0] == {
        "type": "thinking",
        "thinking": "reason",
        "signature": "sig-1",
    }
    openai = CanonicalModelContextAdapter().build([events[0], RuntimeEvent.from_dict(thinking)], provider="openai")
    assert not any("kind" in str(message) for message in openai.messages)

    unsigned = thinking.copy()
    unsigned["id"] = "unsigned-thinking"
    unsigned["content"] = {"kind": "thinking", "text": "untrusted reason"}
    degraded = CanonicalModelContextAdapter().build(
        [events[0], RuntimeEvent.from_dict(unsigned)], provider="anthropic"
    )
    assert not any("kind" in str(message) for message in degraded.messages)
    assert not any(
        item.get("type") == "thinking"
        for message in degraded.messages
        for item in message.get("content", [])
        if isinstance(item, dict)
    )


def test_anthropic_tool_result_serializes_structured_content_as_json_text():
    events = _events("anthropic")
    bounded_ref = {
        "kind": "bounded_ref",
        "ref": "artifact:sha256:abc123",
        "sha256": "sha256:abc123",
        "size_bytes": 24576,
        "inline": "第一行\n第二行",
        "truncated": True,
    }
    response = events[-1].to_dict()
    response["content"]["result"] = bounded_ref

    context = CanonicalModelContextAdapter().build(
        [*events[:-1], RuntimeEvent.from_dict(response)], provider="anthropic"
    )
    tool_result = next(
        block
        for message in context.messages
        if message.get("role") == "user"
        for block in message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    )

    assert isinstance(tool_result["content"], str)
    assert json.loads(tool_result["content"]) == bounded_ref


def test_replay_groups_same_invocation_tool_calls_and_anthropic_batches_results():
    events = _events("anthropic")
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

    records = [
        *events[:3],
        RuntimeEvent.from_dict(second_call),
        *events[3:],
        RuntimeEvent.from_dict(second_response),
    ]
    replay = ModelReplayProjection().build(records)
    assistant_calls = [
        message for message in replay.messages if message.get("tool_calls")
    ]
    assert len(assistant_calls) == 1
    assert [call["id"] for call in assistant_calls[0]["tool_calls"]] == [
        events[2].content["id"],
        "fixture-call-0002",
    ]

    context = CanonicalModelContextAdapter().build(records, provider="anthropic")
    assistant_messages = [
        message
        for message in context.messages
        if message.get("role") == "assistant"
        and any(
            isinstance(block, dict) and block.get("type") == "tool_use"
            for block in message.get("content", [])
        )
    ]
    tool_result_messages = [
        message
        for message in context.messages
        if message.get("role") == "user"
        and isinstance(message.get("content"), list)
        and all(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in message["content"]
        )
    ]
    assert len(assistant_messages) == 1
    assert [block["id"] for block in assistant_messages[0]["content"]] == [
        events[2].content["id"],
        "fixture-call-0002",
    ]
    assert len(tool_result_messages) == 1
    assert [block["tool_use_id"] for block in tool_result_messages[0]["content"]] == [
        events[2].content["id"],
        "fixture-call-0002",
    ]


def test_session_loader_is_canonical_derived_without_legacy_files(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    event = _events()[1]
    with SQLiteRuntimeStore(database) as store:
        store.append(_events()[0])
        store.append(event)
        loaded = load_session(event.session_id, runtime_store=store)
    assert loaded is not None
    assert loaded["metadata"]["source"] == "canonical"
    assert loaded["canonicalMessages"][0]["content"] == event.content["text"]


def test_metrics_projection_rebuilds_supported_facts_without_tracer_input():
    events = _events()
    partial = events[1].to_dict()
    partial.update({"id": "metrics-partial", "ts": events[0].ts + 10, "partial": True})
    usage = events[1].to_dict()
    usage.update({
        "id": "metrics-usage",
        "role": "system",
        "author": "agent",
        "content": None,
        "actions": {"usage": {"input_tokens": 11, "output_tokens": 7}},
        "metadata": {"lifecycle": "usage", "raw_request": "sk-ant-must-not-appear"},
    })
    finish = events[1].to_dict()
    finish.update({
        "id": "metrics-finish",
        "role": "system",
        "author": "agent",
        "content": None,
        "actions": {"model_finish": {"finish_reason": "stop", "latency_ms": 42}},
        "metadata": {"lifecycle": "model_final"},
    })
    outcome = events[5].to_dict()
    outcome["actions"]["tool_outcome"]["duration_ms"] = 9
    terminal = events[1].to_dict()
    terminal.update({
        "id": "metrics-terminal",
        "role": "system",
        "author": "system",
        "status": "completed",
        "actions": {"end_run": True},
        "metadata": {"lifecycle": "run_terminal"},
        "ts": events[0].ts + 100,
    })
    canonical = events + [
        RuntimeEvent.from_dict(partial),
        RuntimeEvent.from_dict(usage),
        RuntimeEvent.from_dict(finish),
        RuntimeEvent.from_dict(outcome),
        RuntimeEvent.from_dict(terminal),
    ]
    projection = CanonicalMetricsProjection().build(canonical)
    run = projection.runs[0]
    assert run["first_token_ms"] == 10
    assert run["first_token_available"] is True
    assert run["input_tokens"] == 11
    assert run["output_tokens"] == 7
    assert run["finish_reason"] == "stop"
    assert run["tool_duration_ms"] == 9
    assert run["terminal_status"] == "completed"
    assert "sk-ant-must-not-appear" not in str(projection.to_dict())
    rebuilt = CanonicalMetricsProjection().build(canonical)
    assert rebuilt.to_dict() == projection.to_dict()
