"""C08 canonical-first projection/replay tests."""

from __future__ import annotations

from pathlib import Path

from mini_claude.projections import ModelReplayProjection, RunTraceProjection, SessionProjection
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


def test_session_loader_can_be_explicitly_canonical_first_without_legacy_files(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    event = _events()[1]
    with SQLiteRuntimeStore(database) as store:
        store.append(event)
        loaded = load_session(event.session_id, runtime_store=store, canonical_first=True)
    assert loaded is not None
    assert loaded["metadata"]["source"] == "canonical"
    assert loaded["canonicalMessages"][0]["content"] == event.content["text"]
