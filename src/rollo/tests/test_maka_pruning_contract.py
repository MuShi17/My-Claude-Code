"""Regression tests for the Maka-aligned tool-result pruning contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from rollo.archive_capability import ToolResultArchiveCapability
from rollo.archive_projection import project_archived_tool_results_outcome
from rollo.artifact_archive import ArtifactArchive
from rollo.projections.model_replay_projection import ModelReplayProjection
from rollo.projections.incremental_replay import IncrementalModelReplayCursor
from rollo.projections.base import EventRecord
from rollo.projections.provider_context import (
    CanonicalModelContextAdapter,
    ProviderRequestCycle,
    ProviderRequestCycleIdentity,
    _provider_request_payload,
)
from rollo.runtime_event import RuntimeEvent
from rollo.runtime_store import SQLiteRuntimeStore
from rollo.tool_result import (
    canonical_tool_result_bytes,
    estimated_tool_result_tokens,
)
from rollo.event_ids import RunContext


def _event(
    *,
    event_id: str,
    turn_id: str,
    run_id: str,
    invocation_id: str,
    role: str,
    author: str,
    content: dict,
    lifecycle: str,
) -> RuntimeEvent:
    return RuntimeEvent.create(
        RunContext(
            session_id="session-prune",
            turn_id=turn_id,
            run_id=run_id,
            invocation_id=invocation_id,
            context_id="context-prune",
        ),
        role=role,
        author=author,
        event_id=event_id,
        content=content,
        metadata={"lifecycle": lifecycle},
        # A function_response is a tool-result payload, not a run terminal event.
        # Marking it completed seals the SQLite run and prevents later steps from
        # being appended, which makes the fixture unlike the production event flow.
        status=None,
    )


def _tool_events(
    *,
    turn_number: int,
    step_number: int,
    body: str,
    tool_name: str = "read_file",
    arguments: dict | None = None,
    invocation_id: str | None = None,
    call_id: str | None = None,
    is_error: bool = False,
) -> list[RuntimeEvent]:
    turn_id = f"turn-{turn_number}"
    run_id = f"run-{turn_number}"
    invocation_id = invocation_id or f"invocation-{turn_number}-{step_number}"
    call_id = call_id or f"call-{turn_number}-{step_number}"
    args = (
        {"file_path": f"./sample-{turn_number}.txt"}
        if arguments is None
        else arguments
    )
    return [
        _event(
            event_id=f"event-user-{turn_number}-{step_number}",
            turn_id=turn_id,
            run_id=run_id,
            invocation_id=invocation_id,
            role="user",
            author="user",
            content={"kind": "text", "text": f"turn {turn_number}"},
            lifecycle="user_input",
        ),
        _event(
            event_id=f"event-call-{turn_number}-{step_number}",
            turn_id=turn_id,
            run_id=run_id,
            invocation_id=invocation_id,
            role="model",
            author="agent",
            content={
                "kind": "function_call",
                "id": call_id,
                "name": tool_name,
                "args": args,
            },
            lifecycle="tool_call_final",
        ),
        _event(
            event_id=f"event-result-{turn_number}-{step_number}",
            turn_id=turn_id,
            run_id=run_id,
            invocation_id=invocation_id,
            role="tool",
            author="tool",
            content={
                "kind": "function_response",
                "id": call_id,
                "name": tool_name,
                "result": body,
                "isError": is_error,
            },
            lifecycle="function_response",
        ),
    ]


def _invocation_opened(turn_number: int, step_number: int) -> RuntimeEvent:
    return _event(
        event_id=f"event-open-{turn_number}-{step_number}",
        turn_id=f"turn-{turn_number}",
        run_id=f"run-{turn_number}",
        invocation_id=f"invocation-{turn_number}-{step_number}",
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
        lifecycle="invocation_opened",
    )


def _replay(events: list[RuntimeEvent]):
    return ModelReplayProjection().build(events)


def _capability(tmp_path: Path) -> ToolResultArchiveCapability:
    return ToolResultArchiveCapability(
        ArtifactArchive(tmp_path / "artifacts"),
        session_id="session-prune",
        run_id="run-3",
    )


def test_prune_estimator_uses_canonical_text_codepoints_and_binary_envelope():
    for value in ["值🙂" * 200, {"b": "😀", "a": ["中"]}, b"\xff" * 128]:
        serialized = canonical_tool_result_bytes(value).decode("utf-8")
        assert estimated_tool_result_tokens(value) == (len(serialized) + 2) // 3
    # The JSON string envelope contributes two quote codepoints: serialized
    # lengths 6,144 and 6,145 are exactly the 2,048/2,049 boundary.
    exact = canonical_tool_result_bytes("x" * 6_142).decode("utf-8")
    over = canonical_tool_result_bytes("x" * 6_143).decode("utf-8")
    assert len(exact) == 6_144
    assert len(over) == 6_145
    assert estimated_tool_result_tokens("x" * 6_142) == 2_048
    assert estimated_tool_result_tokens("x" * 6_143) == 2_049


def test_stale_prune_protects_newest_two_turns_and_ignores_aggregate_budget(tmp_path: Path):
    body = "old result🙂\n" * 900
    events = [
        *_tool_events(turn_number=1, step_number=1, body=body),
        *_tool_events(turn_number=2, step_number=1, body=body),
        *_tool_events(turn_number=3, step_number=1, body=body),
    ]
    replay = _replay(events)
    outcome = project_archived_tool_results_outcome(
        replay.messages,
        _capability(tmp_path),
        message_metadata=replay.message_metadata,
        active_turn_id="turn-3",
        budget_bytes=1,
    )
    tool_messages = [
        message for message in outcome.messages if message.get("role") == "tool"
    ]
    assert tool_messages[0]["content"]["kind"] == "bounded_ref"
    assert tool_messages[1]["content"] == body
    assert tool_messages[2]["content"] == body
    assert len(list((tmp_path / "artifacts").rglob("*.bin"))) == 1


def test_active_prune_keeps_latest_step_until_emergency(tmp_path: Path):
    body = "active result🙂\n" * 900
    first = _tool_events(
        turn_number=1,
        step_number=1,
        body=body,
        arguments={"file_path": "sample.txt", "offset": 0, "limit": 10},
    )
    second = _tool_events(
        turn_number=1,
        step_number=2,
        body=body.replace("active", "latest"),
        arguments={"file_path": "sample.txt", "offset": 10, "limit": 10},
    )
    replay = _replay([*first, *second])
    capability = _capability(tmp_path)
    ordinary = project_archived_tool_results_outcome(
        replay.messages,
        capability,
        message_metadata=replay.message_metadata,
        active_turn_id="turn-1",
        budget_bytes=10**9,
    )
    tools = [message for message in ordinary.messages if message.get("role") == "tool"]
    assert tools[0]["content"]["kind"] == "bounded_ref"
    assert tools[1]["content"] == body.replace("active", "latest")
    assert ordinary.emergency_used is False

    emergency = project_archived_tool_results_outcome(
        replay.messages,
        capability,
        message_metadata=replay.message_metadata,
        active_turn_id="turn-1",
        include_latest_active=True,
        budget_bytes=10**9,
    )
    emergency_tools = [message for message in emergency.messages if message.get("role") == "tool"]
    assert all(item["content"]["kind"] == "bounded_ref" for item in emergency_tools)
    assert emergency.emergency_used is True
    assert len(list((tmp_path / "artifacts").rglob("*.bin"))) == 2


def test_active_read_coverage_supersedes_old_result_even_below_stale_threshold(tmp_path: Path):
    old_body = "covered old result🙂\n" * 100
    new_body = "new covering result🚀\n" * 100
    events = [
        *_tool_events(
            turn_number=1,
            step_number=1,
            body=old_body,
            arguments={"file_path": "./sample.txt", "offset": 10, "limit": 10},
        ),
        *_tool_events(
            turn_number=1,
            step_number=2,
            body=new_body,
            arguments={"file_path": "sample.txt", "offset": 0, "limit": 30},
        ),
    ]
    replay = _replay(events)
    outcome = project_archived_tool_results_outcome(
        replay.messages,
        _capability(tmp_path),
        message_metadata=replay.message_metadata,
        active_turn_id="turn-1",
        budget_bytes=10**9,
    )
    tools = [message for message in outcome.messages if message.get("role") == "tool"]
    assert tools[0]["content"]["kind"] == "bounded_ref"
    assert tools[1]["content"] == new_body


def test_request_cycle_allows_one_emergency_and_reuses_result(tmp_path: Path):
    body = "cycle body🙂\n" * 900
    replay = _replay(_tool_events(turn_number=1, step_number=1, body=body))
    capability = _capability(tmp_path)
    cycle = ProviderRequestCycle(
        ProviderRequestCycleIdentity(
            session_id="session-prune",
            provider_request_id="request-1",
            source_digest=replay.source_digest,
            source_high_water=replay.high_water,
            provider="openai",
            active_turn_id="turn-1",
            system_tools_digest=hashlib.sha256(b"tools").hexdigest(),
            request_budget_bytes=1_000,
        )
    )
    adapter = CanonicalModelContextAdapter()
    first = adapter.build_result(
        replay,
        provider="openai",
        system_prompt="system",
        archive_capability=capability,
        budget_bytes=1_000,
        active_turn_id="turn-1",
        request_cycle=cycle,
    )
    second = adapter.build_result(
        replay,
        provider="openai",
        system_prompt="system",
        archive_capability=capability,
        budget_bytes=1_000,
        active_turn_id="turn-1",
        request_cycle=cycle,
    )
    assert first.emergency_used is True
    assert second.emergency_used is True
    assert first.messages == second.messages
    assert cycle.emergency_used is True
    assert len(list((tmp_path / "artifacts" / "refs").glob("*.json"))) == 1


def test_emergency_requires_budget_and_active_sidecar(tmp_path: Path):
    body = "emergency precondition🙂\n" * 900
    result = _replay(_tool_events(turn_number=1, step_number=1, body=body))
    adapter = CanonicalModelContextAdapter()

    no_budget = adapter.build_result(
        result,
        provider="openai",
        system_prompt="system",
        archive_capability=_capability(tmp_path / "no-budget"),
        budget_bytes=None,
        active_turn_id="turn-1",
    )
    assert no_budget.request_fits is True
    assert no_budget.emergency_used is False
    assert not list((tmp_path / "no-budget").rglob("*.json"))

    without_sidecar = replace(result, message_metadata=())
    cycle = ProviderRequestCycle(
        ProviderRequestCycleIdentity(
            session_id="session-prune",
            provider_request_id="request-no-active",
            source_digest=without_sidecar.source_digest,
            source_high_water=without_sidecar.high_water,
            provider="openai",
            active_turn_id=None,
            system_tools_digest=hashlib.sha256(b"tools").hexdigest(),
            request_budget_bytes=1,
        )
    )
    no_active = adapter.build_result(
        without_sidecar,
        provider="openai",
        system_prompt="system",
        archive_capability=_capability(tmp_path / "no-active"),
        budget_bytes=1,
        active_turn_id=None,
        request_cycle=cycle,
    )
    assert no_active.request_fits is False
    assert no_active.emergency_used is False
    assert cycle.emergency_used is False
    tool = next(message for message in no_active.messages if message.get("role") == "tool")
    assert tool["content"] == body
    assert not list((tmp_path / "no-active").rglob("*.json"))


def test_missing_sidecar_is_fail_open_and_never_archives_new_ref(tmp_path: Path):
    body = "unclassified body🙂\n" * 900
    replay = _replay(_tool_events(turn_number=1, step_number=1, body=body))
    outcome = project_archived_tool_results_outcome(
        replay.messages,
        _capability(tmp_path),
        message_metadata=(),
        active_turn_id="turn-1",
        include_latest_active=True,
        budget_bytes=1,
    )
    tool = next(message for message in outcome.messages if message.get("role") == "tool")
    assert tool["content"] == body
    assert any(item.code == "replay_metadata_unavailable" for item in outcome.diagnostics)
    assert not list((tmp_path / "artifacts").rglob("*.json"))


def test_provider_allowlist_drops_internal_sidecar_fields():
    result = _replay(_tool_events(turn_number=1, step_number=1, body="short"))
    messages = tuple(
        {**message, "_replay_sidecar": {"secret": "must-not-cross"}}
        for message in result.messages
    )
    result = result.__class__(
        projection_version=result.projection_version,
        schema_version=result.schema_version,
        high_water=result.high_water,
        source_digest=result.source_digest,
        digest=result.digest,
        messages=messages,
        partial_count=result.partial_count,
        diagnostics=result.diagnostics,
        context_epoch=result.context_epoch,
        context_id=result.context_id,
        message_metadata=result.message_metadata,
    )
    context = CanonicalModelContextAdapter().build_result(result, provider="openai")
    assert all("_replay_sidecar" not in message for message in context.messages)


def test_descriptor_mapping_normalizes_ranges_and_limits_snapshot_allowlist(tmp_path: Path):
    body = "descriptor result🙂\n" * 100
    events = [
        *_tool_events(
            turn_number=1,
            step_number=1,
            body=body,
            tool_name="read_file",
            arguments={"file_path": "./sample.txt"},
        ),
        *_tool_events(
            turn_number=1,
            step_number=2,
            body=body,
            tool_name="list_files",
            arguments={"pattern": "*.py", "path": "./src"},
        ),
        *_tool_events(
            turn_number=1,
            step_number=3,
            body=body,
            tool_name="grep_search",
            arguments={"pattern": "needle", "path": "./src", "include": "*.py"},
        ),
        *_tool_events(
            turn_number=1,
            step_number=4,
            body=body,
            tool_name="run_shell",
            arguments={"command": "git status --short"},
        ),
        *_tool_events(
            turn_number=1,
            step_number=5,
            body=body,
            tool_name="run_shell",
            arguments={"command": "git status && echo unsafe"},
        ),
    ]
    replay = _replay(events)
    tool_meta = [
        meta
        for message, meta in zip(replay.messages, replay.message_metadata, strict=True)
        if message.get("role") == "tool"
    ]
    assert tool_meta[0].range_identity == ("sample.txt", 0, None)
    assert tool_meta[0].semantic_input_complete is True
    assert tool_meta[1].snapshot_identity == ("Glob", "*.py", "src")
    assert tool_meta[2].snapshot_identity == ("Grep", "needle", "src", "*.py")
    assert tool_meta[3].snapshot_identity == ("Bash", "git status --short")
    assert tool_meta[4].snapshot_identity is None
    assert tool_meta[4].semantic_input_complete is True


def test_openai_json_string_arguments_enable_semantic_supersession(tmp_path: Path):
    body = "openai semantic body🙂\n" * 200
    arguments = json.dumps({"file_path": "sample.txt"}, separators=(",", ":"))
    replay = _replay(
        [
            *_tool_events(
                turn_number=1,
                step_number=1,
                body=body,
                arguments=arguments,
            ),
            *_tool_events(
                turn_number=1,
                step_number=2,
                body=body,
                arguments=arguments,
            ),
        ]
    )
    tool_meta = [
        meta
        for message, meta in zip(replay.messages, replay.message_metadata, strict=True)
        if message.get("role") == "tool"
    ]
    assert all(meta.semantic_input_complete for meta in tool_meta)
    assert all(meta.range_identity == ("sample.txt", 0, None) for meta in tool_meta)

    outcome = project_archived_tool_results_outcome(
        replay.messages,
        _capability(tmp_path),
        message_metadata=replay.message_metadata,
        active_turn_id="turn-1",
        budget_bytes=10**9,
    )
    tool_messages = [message for message in outcome.messages if message.get("role") == "tool"]
    assert tool_messages[0]["content"]["kind"] == "bounded_ref"
    assert tool_messages[1]["content"] == body
    assert len(list((tmp_path / "artifacts").rglob("*.json"))) == 1


def test_invalid_json_string_arguments_remain_incomplete_and_visible(tmp_path: Path):
    body = "invalid argument body🙂\n" * 200
    invalid = '{"file_path":'
    replay = _replay(
        [
            *_tool_events(turn_number=1, step_number=1, body=body, arguments=invalid),
            *_tool_events(turn_number=1, step_number=2, body=body, arguments=invalid),
        ]
    )
    tool_meta = [
        meta
        for message, meta in zip(replay.messages, replay.message_metadata, strict=True)
        if message.get("role") == "tool"
    ]
    assert all(meta.semantic_input_complete is False for meta in tool_meta)
    assert all(meta.range_identity is None for meta in tool_meta)

    outcome = project_archived_tool_results_outcome(
        replay.messages,
        _capability(tmp_path),
        message_metadata=replay.message_metadata,
        active_turn_id="turn-1",
        budget_bytes=10**9,
    )
    tool_messages = [message for message in outcome.messages if message.get("role") == "tool"]
    assert [message["content"] for message in tool_messages] == [body, body]
    assert not list((tmp_path / "artifacts").rglob("*.json"))


def test_semantic_supersession_does_not_guess_parallel_unknown_or_invalid_results(
    tmp_path: Path,
):
    body = "semantic body🙂\n" * 200

    parallel = _replay(
        [
            *_tool_events(
                turn_number=1,
                step_number=1,
                body=body,
                invocation_id="parallel-invocation",
                arguments={"file_path": "sample.txt"},
            ),
            *_tool_events(
                turn_number=1,
                step_number=2,
                body=body,
                invocation_id="parallel-invocation",
                arguments={"file_path": "sample.txt"},
            ),
        ]
    )
    parallel_outcome = project_archived_tool_results_outcome(
        parallel.messages,
        _capability(tmp_path / "parallel"),
        message_metadata=parallel.message_metadata,
        active_turn_id="turn-1",
    )
    parallel_tools = [
        message for message in parallel_outcome.messages if message.get("role") == "tool"
    ]
    assert [message["content"] for message in parallel_tools] == [body, body]

    cases = (
        ("unknown", "unknown_tool", {"value": "same"}, False, False),
        ("invalid", "read_file", {}, False, False),
        ("failure", "read_file", {"file_path": "sample.txt"}, True, False),
    )
    for name, tool_name, arguments, first_is_error, second_is_error in cases:
        events = [
            *_tool_events(
                turn_number=1,
                step_number=1,
                body=body,
                tool_name=tool_name,
                arguments=arguments,
                is_error=first_is_error,
            ),
            *_tool_events(
                turn_number=1,
                step_number=2,
                body=body,
                tool_name=tool_name,
                arguments=arguments,
                is_error=second_is_error,
            ),
        ]
        replay = _replay(events)
        outcome = project_archived_tool_results_outcome(
            replay.messages,
            _capability(tmp_path / name),
            message_metadata=replay.message_metadata,
            active_turn_id="turn-1",
        )
        tools = [message for message in outcome.messages if message.get("role") == "tool"]
        assert [message["content"] for message in tools] == [body, body], name


def test_semantic_supersession_is_limited_to_the_current_active_turn(tmp_path: Path):
    body = "historical duplicate🙂\n" * 200
    replay = _replay(
        [
            *_tool_events(
                turn_number=1,
                step_number=1,
                body=body,
                arguments={"file_path": "sample.txt"},
            ),
            *_tool_events(
                turn_number=1,
                step_number=2,
                body=body,
                arguments={"file_path": "sample.txt"},
            ),
            *_tool_events(turn_number=2, step_number=1, body="current turn"),
        ]
    )
    outcome = project_archived_tool_results_outcome(
        replay.messages,
        _capability(tmp_path),
        message_metadata=replay.message_metadata,
        active_turn_id="turn-2",
    )
    tools = [message for message in outcome.messages if message.get("role") == "tool"]
    assert [message["content"] for message in tools] == [body, body, "current turn"]
    assert not list(tmp_path.rglob("*.json"))


def test_identity_matrix_disables_unsafe_pruning_per_missing_field(tmp_path: Path):
    body = "identity body🙂\n" * 900
    replay = _replay(_tool_events(turn_number=1, step_number=1, body=body))
    tool_index = next(
        index for index, message in enumerate(replay.messages) if message.get("role") == "tool"
    )
    complete = replay.message_metadata[tool_index]
    missing_fields = (
        "runtime_event_id",
        "canonical_ordinal",
        "turn_id",
        "run_id",
        "invocation_id",
        "tool_call_id",
        "tool_name",
        "body_sha256",
    )
    for field in missing_fields:
        partial = replace(complete, identity_state="partial", **{field: None})
        metadata = list(replay.message_metadata)
        metadata[tool_index] = partial
        outcome = project_archived_tool_results_outcome(
            replay.messages,
            _capability(tmp_path / field),
            message_metadata=tuple(metadata),
            active_turn_id="turn-1",
            include_latest_active=True,
        )
        tool = outcome.messages[tool_index]
        assert tool["content"] == body
        assert any(item.code == "archive_identity_unavailable" for item in outcome.diagnostics)
        assert not list((tmp_path / field).rglob("*.json"))


def test_archive_failure_is_fail_open_and_diagnostic_is_stable(tmp_path: Path):
    class Failure:
        def check(self, point: str) -> None:
            if point in {"artifact.write", "archive.write"}:
                raise RuntimeError("fixture archive failure with raw detail")

    body = "failed archive body🙂\n" * 900
    replay = _replay(
        [
            *_tool_events(turn_number=1, step_number=1, body=body),
            *_tool_events(turn_number=2, step_number=1, body="small"),
            *_tool_events(turn_number=3, step_number=1, body="small"),
        ]
    )
    archive = ArtifactArchive(tmp_path / "artifacts", fault_hook=Failure())
    capability = ToolResultArchiveCapability(
        archive,
        session_id="session-prune",
        run_id="run-3",
    )
    first = project_archived_tool_results_outcome(
        replay.messages,
        capability,
        message_metadata=replay.message_metadata,
        active_turn_id="turn-3",
    )
    second = project_archived_tool_results_outcome(
        replay.messages,
        capability,
        message_metadata=replay.message_metadata,
        active_turn_id="turn-3",
    )
    first_tool = next(message for message in first.messages if message.get("role") == "tool")
    second_tool = next(message for message in second.messages if message.get("role") == "tool")
    assert first_tool["content"] == body
    assert second_tool["content"] == body
    assert [item.to_dict() for item in first.diagnostics] == [
        item.to_dict() for item in second.diagnostics
    ]
    assert any(item.code == "archive_write_failed" for item in first.diagnostics)
    assert all("fixture archive failure" not in item.message for item in first.diagnostics)
    assert not list((tmp_path / "artifacts").rglob("*.json"))


def test_cross_run_projection_reuses_one_logical_ref_with_complete_sidecar(tmp_path: Path):
    body = "stable identity🙂\n" * 900
    events = [
        *_tool_events(turn_number=1, step_number=1, body=body),
        *_tool_events(turn_number=2, step_number=1, body="small"),
        *_tool_events(turn_number=3, step_number=1, body="small"),
    ]
    replay = _replay(events)
    archive = ArtifactArchive(tmp_path / "archive")
    capability_a = ToolResultArchiveCapability(
        archive, session_id="session-prune", run_id="run-a"
    )
    capability_b = ToolResultArchiveCapability(
        archive, session_id="session-prune", run_id="run-b"
    )
    outcome_a = project_archived_tool_results_outcome(
        replay.messages,
        capability_a,
        message_metadata=replay.message_metadata,
        active_turn_id="turn-3",
    )
    outcome_b = project_archived_tool_results_outcome(
        replay.messages,
        capability_b,
        message_metadata=replay.message_metadata,
        active_turn_id="turn-3",
    )
    tool_a = next(message for message in outcome_a.messages if message.get("role") == "tool")
    tool_b = next(message for message in outcome_b.messages if message.get("role") == "tool")
    assert tool_a["content"] == tool_b["content"]
    assert tool_a["content"]["ref"].startswith("artifact:tool-result:")
    assert len(list((tmp_path / "archive" / "refs").glob("*.json"))) == 1


def test_cold_and_incremental_sidecars_and_pruning_are_equal(tmp_path: Path):
    body = "parity body🙂\n" * 900
    events = [
        *_tool_events(turn_number=1, step_number=1, body=body),
        *_tool_events(turn_number=2, step_number=1, body=body),
        *_tool_events(turn_number=3, step_number=1, body=body),
        *_tool_events(turn_number=3, step_number=2, body=body.replace("parity", "latest")),
    ]
    cold = _replay(events)
    cursor = IncrementalModelReplayCursor(context_id="context-prune")
    records = [EventRecord(index, event) for index, event in enumerate(events, start=1)]
    cursor.append(records[: len(records) // 2])
    cursor.append(records[len(records) // 2 :])
    warm = cursor.result()
    assert warm.messages == cold.messages
    assert [meta.to_dict() for meta in warm.message_metadata] == [
        meta.to_dict() for meta in cold.message_metadata
    ]

    cold_outcome = project_archived_tool_results_outcome(
        cold.messages,
        _capability(tmp_path / "cold"),
        message_metadata=cold.message_metadata,
        active_turn_id="turn-3",
    )
    warm_outcome = project_archived_tool_results_outcome(
        warm.messages,
        _capability(tmp_path / "warm"),
        message_metadata=warm.message_metadata,
        active_turn_id="turn-3",
    )
    assert cold_outcome.messages == warm_outcome.messages
    assert [item.code for item in cold_outcome.diagnostics] == [
        item.code for item in warm_outcome.diagnostics
    ]
    assert len(list((tmp_path / "cold").rglob("*.json"))) == 2
    assert len(list((tmp_path / "warm").rglob("*.json"))) == 2


def test_sqlite_reopen_rebuilds_the_same_sidecar_and_projection(tmp_path: Path):
    body = "sqlite parity🙂\n" * 900
    events = [
        *_tool_events(turn_number=1, step_number=1, body=body),
        *_tool_events(turn_number=2, step_number=1, body=body),
        *_tool_events(turn_number=3, step_number=1, body=body),
        *_tool_events(
            turn_number=3,
            step_number=2,
            body=body.replace("sqlite", "latest"),
        ),
    ]
    database = tmp_path / "canonical.sqlite"
    with SQLiteRuntimeStore(database) as store:
        stored_events: list[RuntimeEvent] = []
        for turn_number, step_number in ((1, 1), (2, 1), (3, 1), (3, 2)):
            stored_events.append(_invocation_opened(turn_number, step_number))
            start = (turn_number, step_number)
            stored_events.extend(
                event
                for event in events
                if event.invocation_id == f"invocation-{start[0]}-{start[1]}"
            )
        for event in stored_events:
            store.append(event)
        cold = ModelReplayProjection().build(store, context_id="context-prune")
        cursor = IncrementalModelReplayCursor(context_id="context-prune")
        cursor.append(
            EventRecord(ordinal, event)
            for ordinal, event in store.read_event_records(context_id="context-prune")
        )
        warm = cursor.result()
        assert warm.messages == cold.messages
        assert [item.to_dict() for item in warm.message_metadata] == [
            item.to_dict() for item in cold.message_metadata
        ]

    with SQLiteRuntimeStore(database) as reopened:
        rebuilt = ModelReplayProjection().build(
            reopened,
            context_id="context-prune",
        )
        assert rebuilt.messages == cold.messages
        assert [item.to_dict() for item in rebuilt.message_metadata] == [
            item.to_dict() for item in cold.message_metadata
        ]


def test_provider_tool_definition_allowlist_excludes_runtime_metadata():
    definitions = [
        {
            "name": "fixture",
            "description": "fixture description",
            "input_schema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
            "deferred": True,
            "_replay_sidecar": {"secret": "must-not-cross"},
        }
    ]
    anthropic = _provider_request_payload(
        "anthropic", (), provider_tools=definitions, system_prompt="system"
    )
    openai = _provider_request_payload(
        "openai", (), provider_tools=definitions, system_prompt="system"
    )
    assert anthropic["tools"] == [
        {
            "name": "fixture",
            "description": "fixture description",
            "input_schema": definitions[0]["input_schema"],
        }
    ]
    assert openai["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "fixture",
                "description": "fixture description",
                "parameters": definitions[0]["input_schema"],
            },
        }
    ]
    encoded = str(openai)
    assert "_replay_sidecar" not in encoded
    assert "deferred" not in encoded
