"""P0 regressions for canonical final calls, ArchiveRead and run metrics."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rollo.archive_capability import ToolResultArchiveCapability
from rollo.artifact_archive import ArtifactArchive
from rollo.event_ids import RunContext
from rollo.event_sink import (
    CanonicalToolCallConflictError,
    RecordingEventSink,
    RuntimeEventEmitter,
)
from rollo.projections.base import RuntimeEventReducer, EventRecord
from rollo.projections.metrics_projection import CanonicalMetricsProjection
from rollo.projections.model_replay_projection import ModelReplayProjection
from rollo.projections.provider_context import CanonicalModelContextAdapter
from rollo.projections.session_projection import SessionProjection
from rollo.projections.incremental_replay import IncrementalModelReplayCursor
from rollo.runtime_event import RuntimeEvent
from rollo.runtime_lifecycle import DurableToolBoundary, ModelCallRecorder
from rollo.runtime_store import SQLiteRuntimeStore
from rollo.tool_call_identity import is_final_tool_call


def _context(*, invocation_id: str = "invocation-p0") -> RunContext:
    return RunContext(
        "session-p0",
        "turn-p0",
        "run-p0",
        invocation_id,
    )


def _event(
    context: RunContext,
    *,
    event_id: str,
    ts: int,
    content: dict | None = None,
    actions: dict | None = None,
    metadata: dict | None = None,
    role: str = "system",
    author: str = "agent",
    status: str | None = None,
    partial: bool = False,
) -> RuntimeEvent:
    return RuntimeEvent.create(
        context,
        event_id=event_id,
        ts=ts,
        role=role,
        author=author,
        content=content,
        actions=actions,
        metadata=metadata,
        status=status,
        partial=partial,
    )


def test_provider_and_durable_boundary_share_one_final_call_and_one_side_effect(
    tmp_path: Path,
):
    async def scenario() -> None:
        with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
            sink = store
            emitter = RuntimeEventEmitter(sink)
            context = _context()
            recorder = ModelCallRecorder(
                emitter,
                context,
                provider="fixture",
                model="fixture-model",
            )
            recorder.start("request-p0")
            recorder.final_tool_call(
                "call-p0",
                "read_file",
                '{"b":2,"a":1}',
            )

            calls: list[str] = []
            boundary = DurableToolBoundary(emitter, context)
            first = await boundary.execute(
                call_id="call-p0",
                name="read_file",
                arguments={"a": 1, "b": 2},
                executor=lambda: calls.append("first") or "ok",
            )
            second = await boundary.execute(
                call_id="call-p0",
                name="read_file",
                arguments={"b": 2, "a": 1},
                executor=lambda: calls.append("second") or "wrong",
            )

            events = [event for _ordinal, event in store.read_event_records()]
            final_calls = [event for event in events if is_final_tool_call(event)]
            assert len(final_calls) == 1
            assert first.result == second.result == "ok"
            assert calls == ["first"]

    asyncio.run(scenario())


def test_same_run_call_id_is_idempotent_across_model_invocations(tmp_path: Path):
    async def scenario() -> None:
        with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
            emitter = RuntimeEventEmitter(store)
            first_context = _context(invocation_id="invocation-first")
            second_context = _context(invocation_id="invocation-second")
            ModelCallRecorder(
                emitter, first_context, provider="fixture", model="fixture-model"
            ).start("invocation-first")
            calls: list[str] = []
            first = await DurableToolBoundary(emitter, first_context).execute(
                call_id="call-cross-invocation",
                name="read_file",
                arguments={"file_path": "a"},
                executor=lambda: calls.append("first") or "ok",
            )
            ModelCallRecorder(
                emitter, second_context, provider="fixture", model="fixture-model"
            ).start("invocation-second")
            second = await DurableToolBoundary(emitter, second_context).execute(
                call_id="call-cross-invocation",
                name="read_file",
                arguments={"file_path": "a"},
                executor=lambda: calls.append("second") or "wrong",
            )
            assert first.result == second.result == "ok"
            assert first.operation_id == second.operation_id
            assert calls == ["first"]
            assert len(store.read_tool_operations(run_id="run-p0")) == 1

    asyncio.run(scenario())


def test_final_call_conflict_is_fail_closed_and_partial_is_not_a_call():
    sink = RecordingEventSink()
    emitter = RuntimeEventEmitter(sink)
    context = _context()
    recorder = ModelCallRecorder(
        emitter,
        context,
        provider="fixture",
        model="fixture-model",
    )
    recorder.start("request-p0")
    recorder.partial_tool_arguments("call-p0", "read_file", '{"file_path":"a')
    non_final = _event(
        context,
        event_id="non-final",
        ts=2,
        content={"kind": "function_call", "id": "call-p0", "name": "read_file", "args": {}},
        metadata={"lifecycle": "model_final"},
        role="model",
    )
    emitter.emit(non_final)
    recorder.final_tool_call("call-p0", "read_file", {"file_path": "a"})

    with pytest.raises(CanonicalToolCallConflictError, match="call_identity_conflict"):
        recorder.final_tool_call("call-p0", "write_file", {"file_path": "a"})

    records = [EventRecord(index, event) for index, event in enumerate(sink.events, start=1)]
    reduced = RuntimeEventReducer(records)
    assert len(reduced.calls) == 1
    assert len(reduced.partial) == 1
    assert any(item.code == "non_final_function_call" for item in reduced.diagnostics)
    assert not any(item.code == "duplicate_call" for item in reduced.diagnostics)


def test_historical_equivalent_duplicates_are_deterministic_in_a_fresh_process(
    tmp_path: Path,
):
    database = tmp_path / "runtime.sqlite"
    context = _context()
    with SQLiteRuntimeStore(database) as store:
        emitter = RuntimeEventEmitter(store)
        recorder = ModelCallRecorder(
            emitter,
            context,
            provider="fixture",
            model="fixture-model",
        )
        recorder.start("request-p0")
        first = recorder.final_tool_call("call-p0", "read_file", {"file_path": "a"})
        boundary = DurableToolBoundary(emitter, context)
        asyncio.run(
            boundary.execute(
                call_id="call-p0",
                name="read_file",
                arguments={"file_path": "a"},
                executor=lambda: "ok",
            )
        )
        duplicate = _event(
            context,
            event_id="historical-duplicate",
            ts=first.ts + 100,
            content={"kind": "function_call", "id": "call-p0", "name": "read_file", "args": {"file_path": "a"}},
            metadata={"lifecycle": "tool_call_final"},
            role="model",
        )
        store.append(duplicate)

    script = r'''
import asyncio, json, sys
from rollo.event_ids import RunContext
from rollo.event_sink import RuntimeEventEmitter
from rollo.projections.model_replay_projection import ModelReplayProjection
from rollo.projections.provider_context import CanonicalModelContextAdapter
from rollo.runtime_event import canonical_json_bytes
from rollo.runtime_lifecycle import DurableToolBoundary
from rollo.runtime_store import SQLiteRuntimeStore

async def main(path):
    with SQLiteRuntimeStore(path) as store:
        context = RunContext("session-p0", "turn-p0", "run-p0", "invocation-p0")
        calls = []
        boundary = DurableToolBoundary(RuntimeEventEmitter(store), context)
        result = await boundary.execute(
            call_id="call-p0", name="read_file", arguments={"file_path": "a"},
            executor=lambda: calls.append("executed") or "wrong",
        )
        replay = ModelReplayProjection().build(store)
        provider = CanonicalModelContextAdapter().build(
            store,
            provider="openai",
            system_prompt="fixture system",
        )
        print(json.dumps({
            "calls": calls,
            "result": result.result,
            "call_messages": sum(bool(m.get("tool_calls")) for m in replay.messages),
            "provider_call_messages": sum(bool(m.get("tool_calls")) for m in provider.messages),
            "provider_wire_size": len(canonical_json_bytes(list(provider.messages))),
        }))

asyncio.run(main(sys.argv[1]))
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[2])
    result = subprocess.run(
        [sys.executable, "-c", script, str(database)],
        cwd=Path(__file__).parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    output = json.loads(result.stdout.strip().splitlines()[-1])
    assert output["calls"] == []
    assert output["result"] == "ok"
    assert output["call_messages"] == 1
    assert output["provider_call_messages"] == 1
    assert output["provider_wire_size"] > 0


def test_historical_conflicting_duplicate_is_not_executable_in_any_replay_projection():
    context = _context()
    first_call = _event(
        context,
        event_id="historical-first-call",
        ts=1,
        content={
            "kind": "function_call",
            "id": "call-conflict",
            "name": "read_file",
            "args": {"file_path": "safe.txt"},
        },
        metadata={"lifecycle": "tool_call_final"},
        role="model",
    )
    response = _event(
        context,
        event_id="historical-response",
        ts=2,
        content={
            "kind": "function_response",
            "id": "call-conflict",
            "name": "read_file",
            "result": "safe result",
        },
        role="tool",
    )
    conflicting = _event(
        _context(invocation_id="invocation-conflict"),
        event_id="historical-conflict",
        ts=3,
        content={
            "kind": "function_call",
            "id": "call-conflict",
            "name": "write_file",
            "args": {"file_path": "unsafe.txt", "content": "overwrite"},
        },
        metadata={"lifecycle": "tool_call_final"},
        role="model",
    )
    records = [
        EventRecord(1, first_call),
        EventRecord(2, response),
        EventRecord(3, conflicting),
    ]

    replay = ModelReplayProjection().build(records)
    session = SessionProjection().build(records)
    provider = CanonicalModelContextAdapter().build_result(
        replay,
        provider="openai",
        system_prompt="fixture system",
    )
    assert any(item.code == "call_identity_conflict" for item in replay.diagnostics)
    assert not any(message.get("tool_calls") for message in replay.messages)
    assert not any(message.get("tool_calls") for message in session.messages)
    assert not any(message.get("tool_calls") for message in provider.messages)

    cursor = IncrementalModelReplayCursor()
    cursor.append(records)
    assert not any(message.get("tool_calls") for message in cursor.result().messages)
    assert any(item.code == "call_identity_conflict" for item in cursor.result().diagnostics)

    metrics = CanonicalMetricsProjection().build(records)
    assert metrics.runs[0]["tool_calls"] == 0
    assert any(item.code == "call_identity_conflict" for item in metrics.diagnostics)


def test_archive_read_uses_character_and_byte_ranges_and_rejects_beyond_eof(
    tmp_path: Path,
):
    archive = ArtifactArchive(tmp_path / "artifacts")
    text_ref = archive.archive("a🙂中b", mime_type="text/plain", encoding="utf-8", scope="tool-result")
    binary_ref = archive.archive(b"\x00\x01\x02\x03", mime_type="application/octet-stream", encoding="binary", scope="tool-result")
    capability = ToolResultArchiveCapability(archive, session_id="session-p0")
    capability.register_ref(text_ref)
    capability.register_ref(binary_ref)

    text_page = json.loads(capability.execute({"operation": "read", "ref": text_ref.ref, "offset": 1, "limit": 2}))
    assert text_page["page"] == "🙂中"
    assert text_page["unit"] == "chars"
    assert text_page["total_units"] == 4
    assert text_page["next_offset"] == 3
    assert text_page["has_more"] is True

    binary_page = json.loads(capability.execute({"operation": "read", "ref": binary_ref.ref, "offset": 1, "limit": 2}))
    assert binary_page["page"] == "base64:AQI="
    assert binary_page["unit"] == "bytes"
    assert binary_page["next_offset"] == 3

    binary_eof = json.loads(
        capability.execute(
            {"operation": "read", "ref": binary_ref.ref, "offset": 4, "limit": 2}
        )
    )
    assert binary_eof["page"] == "base64:"
    assert binary_eof["page_encoding"] == "base64"
    assert binary_eof["unit"] == "bytes"
    assert binary_eof["next_offset"] == 4
    assert binary_eof["has_more"] is False

    empty_ref = archive.archive(
        "", mime_type="text/plain", encoding="utf-8", scope="tool-result"
    )
    capability.register_ref(empty_ref)
    empty_eof = json.loads(
        capability.execute(
            {"operation": "read", "ref": empty_ref.ref, "offset": 0, "limit": 2}
        )
    )
    assert empty_eof["page"] == ""
    assert empty_eof["total_units"] == 0
    assert empty_eof["has_more"] is False

    eof = json.loads(capability.execute({"operation": "read", "ref": text_ref.ref, "offset": 4, "limit": 2}))
    assert eof["kind"] == "archive_page"
    assert eof["page"] == ""
    assert eof["next_offset"] == 4
    assert eof["has_more"] is False

    beyond = json.loads(capability.execute({"operation": "read", "ref": text_ref.ref, "offset": 5, "limit": 2}))
    assert beyond["kind"] == "archive_read_error"
    assert beyond["error_type"] == "invalid_range"

    for offset in (-1, "1", True):
        invalid = json.loads(capability.execute({"operation": "read", "ref": text_ref.ref, "offset": offset, "limit": 2}))
        assert invalid["error_type"] == "invalid_range"
    for limit in (0, -1, "2", True):
        invalid = json.loads(capability.execute({"operation": "read", "ref": text_ref.ref, "offset": 0, "limit": limit}))
        assert invalid["error_type"] == "invalid_range"
    too_large = json.loads(capability.execute({"operation": "read", "ref": text_ref.ref, "offset": 0, "limit": 7_501}))
    assert too_large["error_type"] == "limit_exceeded"

    forbidden_ref = archive.archive(
        "forbidden",
        mime_type="text/plain",
        encoding="utf-8",
        scope="secret",
    )
    capability.register_ref(forbidden_ref)
    forbidden = json.loads(
        capability.execute({"operation": "inspect", "ref": forbidden_ref.ref})
    )
    assert forbidden["error_type"] == "scope_denied"

    integrity_ref = archive.archive(
        "integrity",
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
    )
    archive._paths(integrity_ref.digest)[0].write_text("tampered", encoding="utf-8")
    integrity = json.loads(
        capability.execute({"operation": "inspect", "ref": integrity_ref.ref})
    )
    assert integrity["error_type"] == "integrity_mismatch"
    assert "traceback" not in json.dumps(integrity).lower()

    long_ref = archive.archive(
        "x" * 49_976,
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
    )
    capability.register_ref(long_ref)
    beyond_long = json.loads(
        capability.execute(
            {"operation": "read", "ref": long_ref.ref, "offset": 64_000, "limit": 32}
        )
    )
    assert beyond_long["error_type"] == "invalid_range"


def test_metrics_keep_first_run_start_and_ignore_partial_first_token():
    context = _context()
    records = [
        _event(
            context,
            event_id="open-run",
            ts=100,
            content={
                "kind": "invocation_opened",
                "protocol": "invocation_opened_v1",
                "route": {"provider": "runtime", "model": "fixture"},
                "configuration": {"attempt": 1},
                "root": {"kind": "agent"},
                "source": {"kind": "fresh"},
            },
            metadata={"lifecycle": "invocation_opened"},
        ),
        _event(
            _context(invocation_id="invocation-second"),
            event_id="open-second",
            ts=150,
            content={
                "kind": "invocation_opened",
                "protocol": "invocation_opened_v1",
                "route": {"provider": "fixture", "model": "fixture"},
                "configuration": {"attempt": 2},
                "root": {"kind": "agent"},
                "source": {"kind": "continuation"},
            },
            metadata={"lifecycle": "invocation_opened"},
        ),
        _event(
            context,
            event_id="partial-token",
            ts=101,
            content={"kind": "text", "text": "partial"},
            metadata={"lifecycle": "stream_partial"},
            role="model",
            partial=True,
        ),
        _event(
            context,
            event_id="first-token",
            ts=120,
            actions={"first_token": {"is_thinking": False}},
            metadata={"lifecycle": "first_token"},
        ),
        _event(
            context,
            event_id="terminal",
            ts=300,
            actions={"end_run": True},
            metadata={"lifecycle": "run_terminal"},
            status="completed",
        ),
    ]
    result = CanonicalMetricsProjection().build(records)
    run = result.runs[0]
    assert run["started_at_ms"] == 100
    assert run["first_token_ms"] == 20
    assert run["duration_ms"] == 200
    assert [item["invocation_id"] for item in run["invocations"]] == [
        "invocation-p0",
        "invocation-second",
    ]
    assert run["invocations"][0]["first_token_ms"] == 20


def test_metrics_bound_missing_terminal_and_regressed_timestamps():
    context = _context()
    opening = _event(
        context,
        event_id="metrics-open",
        ts=100,
        content={
            "kind": "invocation_opened",
            "protocol": "invocation_opened_v1",
            "route": {"provider": "runtime", "model": "fixture"},
            "configuration": {"attempt": 1},
            "root": {"kind": "agent"},
            "source": {"kind": "fresh"},
        },
        metadata={"lifecycle": "invocation_opened"},
    )
    regressed_first_token = _event(
        context,
        event_id="metrics-regressed-token",
        ts=90,
        actions={"first_token": True},
        metadata={"lifecycle": "first_token"},
    )
    missing_terminal = CanonicalMetricsProjection().build(
        [EventRecord(1, opening), EventRecord(2, regressed_first_token)]
    ).runs[0]
    assert missing_terminal["first_token_ms"] == 0
    assert missing_terminal["duration_ms"] is None

    regressed_terminal = _event(
        context,
        event_id="metrics-regressed-terminal",
        ts=80,
        actions={"end_run": True},
        metadata={"lifecycle": "run_terminal"},
        status="completed",
    )
    bounded = CanonicalMetricsProjection().build(
        [
            EventRecord(1, opening),
            EventRecord(2, regressed_first_token),
            EventRecord(3, regressed_terminal),
        ]
    ).runs[0]
    assert bounded["first_token_ms"] >= 0
    assert bounded["duration_ms"] >= 0


def test_metrics_reject_multiple_terminal_boundaries_without_mixing_status_and_time():
    context = _context()
    opening = _event(
        context,
        event_id="terminal-open",
        ts=100,
        content={
            "kind": "invocation_opened",
            "protocol": "invocation_opened_v1",
            "route": {"provider": "runtime", "model": "fixture"},
            "configuration": {"attempt": 1},
            "root": {"kind": "agent"},
            "source": {"kind": "fresh"},
        },
        metadata={"lifecycle": "invocation_opened"},
    )
    first_terminal = _event(
        context,
        event_id="terminal-first",
        ts=200,
        actions={"end_run": True},
        metadata={"lifecycle": "run_terminal"},
        status="completed",
    )
    second_terminal = _event(
        _context(invocation_id="invocation-second"),
        event_id="terminal-second",
        ts=300,
        actions={"end_run": True},
        metadata={"lifecycle": "run_terminal"},
        status="failed",
    )
    result = CanonicalMetricsProjection().build(
        [
            EventRecord(1, opening),
            EventRecord(2, first_terminal),
            EventRecord(3, second_terminal),
        ]
    )
    run = result.runs[0]
    assert run["ended_at_ms"] == 200
    assert run["terminal_status"] == "completed"
    assert run["duration_ms"] == 100
    assert any(item.code == "multiple_terminal_events" for item in result.diagnostics)
