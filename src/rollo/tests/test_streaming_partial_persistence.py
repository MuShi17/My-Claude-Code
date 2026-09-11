"""Streaming partial snapshot and canonical SQLite boundary contracts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from rollo.event_ids import RunContext
from rollo.event_sink import (
    CanonicalSinkError,
    CanonicalSink,
    RecordingEventSink,
    RuntimeEventEmitter,
)
from rollo.recovery import RecoveryProjection
from rollo.runtime_event import RuntimeEvent, canonical_json_bytes
from rollo.runtime_lifecycle import ModelCallRecorder
from rollo.runtime_store import (
    CorruptionError,
    IdempotencyConflictError,
    SQLiteRuntimeStore,
    StoreFaultError,
    StoreValidationError,
)


def _context() -> RunContext:
    return RunContext("stream-session", "stream-turn", "stream-run", "stream-invocation")


def _recorder(store, **kwargs) -> ModelCallRecorder:
    recorder = ModelCallRecorder(
        RuntimeEventEmitter(store),
        _context(),
        provider="fixture",
        model="fixture-model",
        clock=lambda: 1_700_000_000_000,
        **kwargs,
    )
    recorder.start("stream-request")
    return recorder


def _legacy_recorder(sink: RecordingEventSink) -> ModelCallRecorder:
    recorder = ModelCallRecorder(
        RuntimeEventEmitter(CanonicalSink(sink)),
        _context(),
        provider="fixture",
        model="fixture-model",
        clock=lambda: 1_700_000_000_000,
        partial_flush_interval_ms=600_000,
    )
    recorder.start("legacy-request")
    return recorder


def test_first_partial_is_snapshot_then_later_delta_is_batched_and_final_cleans_up(tmp_path: Path):
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        recorder = _recorder(store)

        recorder.partial_text("a")
        assert len(store.read_events()) == 1
        first = store.read_runtime_stream_partials()
        assert len(first) == 1
        assert first[0].payload["content"]["text"] == "a"
        assert first[0].fragment_count == 1

        recorder.partial_text("b")
        assert store.read_runtime_stream_partials()[0].payload["content"]["text"] == "a"
        recorder.flush_partials()
        merged = store.read_runtime_stream_partials()[0]
        assert merged.payload["content"]["text"] == "ab"
        assert merged.fragment_count == 2

        recorder.final_text("ab")
        assert store.read_runtime_stream_partials() == []
        events = store.read_events()
        assert not any(event.partial for event in events)
        assert events[-1].content["text"] == "ab"


def test_partial_streams_are_isolated_and_byte_threshold_flushes(tmp_path: Path):
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        recorder = _recorder(store, partial_batch_max_bytes=1)
        recorder.partial_text("a")
        recorder.partial_text("b")
        recorder.partial_text("think", kind="thinking")
        recorder.partial_tool_arguments("call-a", "read_file", '{"f')
        recorder.partial_tool_arguments("call-b", "write_file", '{"x')

        snapshots = store.read_runtime_stream_partials()
        assert len(snapshots) == 4
        by_kind = {(item.stream_kind, item.tool_call_id): item for item in snapshots}
        assert by_kind[("text", None)].payload["content"]["text"] == "ab"
        assert by_kind[("thinking", None)].payload["content"]["text"] == "think"
        assert by_kind[("function_call", "call-a")].payload["content"]["args"] == '{"f'
        assert by_kind[("function_call", "call-b")].payload["content"]["args"] == '{"x'


def test_partial_timer_flushes_on_the_current_event_loop(tmp_path: Path):
    async def scenario():
        with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
            recorder = _recorder(
                store,
                partial_flush_interval_ms=10,
                partial_batch_max_bytes=1024 * 1024,
            )
            recorder.partial_text("a")
            recorder.partial_text("b")
            await asyncio.sleep(0.05)
            assert store.read_runtime_stream_partials()[0].payload["content"]["text"] == "ab"

    asyncio.run(scenario())


def test_timer_failure_keeps_buffer_and_surfaces_on_next_delta(tmp_path: Path):
    class CommitFault:
        enabled = False

        def check(self, point: str) -> None:
            if self.enabled and point == "store.commit":
                raise RuntimeError("timer commit failed")

    async def scenario():
        fault = CommitFault()
        with SQLiteRuntimeStore(tmp_path / "runtime.sqlite", fault_hook=fault) as store:
            recorder = _recorder(
                store,
                partial_flush_interval_ms=10,
                partial_batch_max_bytes=1024 * 1024,
            )
            recorder.partial_text("a")
            recorder.partial_text("b")
            fault.enabled = True
            await asyncio.sleep(0.05)
            assert store.read_runtime_stream_partials()[0].payload["content"]["text"] == "a"

            fault.enabled = False
            with pytest.raises(StoreFaultError, match="timer commit failed"):
                recorder.partial_text("c")
            recorder.flush_partials()
            assert store.read_runtime_stream_partials()[0].payload["content"]["text"] == "ab"
            recorder.partial_text("c")
            recorder.flush_partials()
            assert store.read_runtime_stream_partials()[0].payload["content"]["text"] == "abc"
            recorder.retry(reason="provider retry")
            recorder.partial_text("d")
            assert any(
                snapshot.payload["content"]["text"] == "d"
                for snapshot in store.read_runtime_stream_partials()
            )

    asyncio.run(scenario())


def test_selective_flush_keeps_timer_error_until_all_buffers_are_recovered(
    tmp_path: Path,
):
    class CommitFault:
        enabled = False

        def check(self, point: str) -> None:
            if self.enabled and point == "store.commit":
                raise RuntimeError("timer commit failed")

    async def scenario():
        fault = CommitFault()
        with SQLiteRuntimeStore(tmp_path / "runtime.sqlite", fault_hook=fault) as store:
            recorder = _recorder(
                store,
                partial_flush_interval_ms=10,
                partial_batch_max_bytes=1024 * 1024,
            )
            text_anchor = recorder.partial_text("a")
            recorder.partial_tool_arguments("call-a", "read_file", "{")
            recorder.partial_text("b")
            recorder.partial_tool_arguments("call-a", "read_file", '"path"')
            fault.enabled = True
            await asyncio.sleep(0.05)
            fault.enabled = False

            text_key = text_anchor.metadata["partial_stream_key"]
            recorder.flush_partials(stream_keys=[text_key])
            assert (
                next(
                    item
                    for item in store.read_runtime_stream_partials()
                    if item.stream_key == text_key
                ).payload["content"]["text"]
                == "ab"
            )
            with pytest.raises(StoreFaultError, match="timer commit failed"):
                recorder.partial_text("c")

            recorder.flush_partials()
            recorder.partial_text("c")
            recorder.flush_partials()
            assert (
                next(
                    item
                    for item in store.read_runtime_stream_partials()
                    if item.stream_key == text_key
                ).payload["content"]["text"]
                == "abc"
            )

    asyncio.run(scenario())


def test_repeated_partial_batch_is_idempotent(tmp_path: Path):
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        recorder = _recorder(store)
        recorder.partial_text("a")
        second = recorder.partial_text("b")
        recorder.flush_partials()
        recorder.emitter.emit_partial_batch([second])
        snapshot = store.read_runtime_stream_partials()[0]
        assert snapshot.payload["content"]["text"] == "ab"
        assert snapshot.fragment_count == 2


def test_optimized_partial_batch_without_sequence_fails_before_transaction(tmp_path: Path):
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        _recorder(store)
        first = RuntimeEvent.create(
            _context(),
            role="model",
            author="agent",
            partial=True,
            content={"kind": "text", "text": "a"},
            metadata={"lifecycle": "stream_partial"},
        )
        second = RuntimeEvent.create(
            _context(),
            role="model",
            author="agent",
            partial=True,
            content={"kind": "text", "text": "b"},
            metadata={"lifecycle": "stream_partial"},
        )
        statements: list[str] = []
        store.connection.set_trace_callback(statements.append)

        with pytest.raises(StoreValidationError, match="positive partial_seq"):
            store.append_runtime_partial_batch([first, second])

        assert not any(statement.upper().startswith("BEGIN") for statement in statements)
        assert store.read_runtime_stream_partials() == []


def test_same_partial_sequence_with_different_payload_fails_closed(tmp_path: Path):
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        recorder = _recorder(store)
        first = recorder.partial_text("a")
        conflict = RuntimeEvent.create(
            _context(),
            role="model",
            author="agent",
            partial=True,
            content={"kind": "text", "text": "different"},
            metadata=dict(first.metadata),
        )

        with pytest.raises(IdempotencyConflictError):
            store.append_runtime_partial_batch([conflict])
        assert store.read_runtime_stream_partials()[0].payload["content"]["text"] == "a"

        gap_metadata = dict(first.metadata)
        gap_metadata["partial_seq"] = 3
        gap = RuntimeEvent.create(
            _context(),
            role="model",
            author="agent",
            partial=True,
            content={"kind": "text", "text": "gap"},
            metadata=gap_metadata,
        )
        with pytest.raises(IdempotencyConflictError, match="sequence gap"):
            store.append_runtime_partial_batch([gap])


@pytest.mark.parametrize("terminal", ["error", "finish", "budget"])
def test_sqlite_terminal_cleanup_rolls_back_and_retries(
    tmp_path: Path, terminal: str
):
    class CommitFault:
        commit_count = 0
        fail_at: int | None = None

        def check(self, point: str) -> None:
            if point != "store.commit":
                return
            self.commit_count += 1
            if self.fail_at == self.commit_count:
                raise RuntimeError("terminal commit failed")

    fault = CommitFault()
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite", fault_hook=fault) as store:
        recorder = _recorder(store)
        recorder.partial_text("pending")
        assert store.read_runtime_stream_partials()

        # error()/finish() write a usage event before their atomic final
        # boundary; budget_exceeded() goes directly to that boundary.
        fault.fail_at = fault.commit_count + (2 if terminal in {"error", "finish"} else 1)
        with pytest.raises(StoreFaultError, match="terminal commit failed"):
            if terminal == "error":
                recorder.error(RuntimeError("provider failed"))
            elif terminal == "finish":
                recorder.finish("stop")
            else:
                recorder.budget_exceeded("run budget reached")

        assert recorder._finished is False
        assert store.read_runtime_stream_partials()[0].payload["content"]["text"] == "pending"

        fault.fail_at = None
        if terminal == "error":
            recorder.error(RuntimeError("provider failed"))
        elif terminal == "finish":
            recorder.finish("stop")
        else:
            recorder.budget_exceeded("run budget reached")

        assert recorder._finished is True
        assert store.read_runtime_stream_partials() == []
        assert not any(event.partial for event in store.read_events())


def test_partial_stream_identity_matrix_fails_closed(tmp_path: Path):
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        recorder = _recorder(store)
        text_anchor = recorder.partial_text("base")
        text_metadata = dict(text_anchor.metadata)
        text_key = text_metadata["partial_stream_key"]

        def partial_event(context: RunContext, content: dict, metadata: dict) -> RuntimeEvent:
            return RuntimeEvent.create(
                context,
                role="model",
                author="agent",
                partial=True,
                content=content,
                metadata=metadata,
            )

        with pytest.raises(IdempotencyConflictError, match="identity conflict"):
            store.append_runtime_partial_batch(
                [
                    partial_event(
                        _context(),
                        {"kind": "text", "text": "attempt"},
                        {**text_metadata, "attempt_id": "different-attempt", "partial_seq": 2},
                    )
                ]
            )
        with pytest.raises(IdempotencyConflictError, match="identity conflict"):
            store.append_runtime_partial_batch(
                [
                    partial_event(
                        _context(),
                        {"kind": "thinking", "text": "kind"},
                        {**text_metadata, "partial_seq": 2},
                    )
                ]
            )
        with pytest.raises(StoreValidationError, match="does not match"):
            store.append_runtime_partial_batch(
                [
                    partial_event(
                        RunContext("other-session", "stream-turn", "stream-run", "stream-invocation"),
                        {"kind": "text", "text": "session"},
                        {**text_metadata, "partial_seq": 2},
                    )
                ]
            )
        with pytest.raises(StoreValidationError, match="existing run"):
            store.append_runtime_partial_batch(
                [
                    partial_event(
                        RunContext("stream-session", "stream-turn", "other-run", "stream-invocation"),
                        {"kind": "text", "text": "run"},
                        {**text_metadata, "partial_seq": 2},
                    )
                ]
            )
        with pytest.raises(StoreValidationError, match="opening event"):
            store.append_runtime_partial_batch(
                [
                    partial_event(
                        RunContext("stream-session", "stream-turn", "stream-run", "other-invocation"),
                        {"kind": "text", "text": "invocation"},
                        {**text_metadata, "partial_seq": 2},
                    )
                ]
            )

        tool_anchor = recorder.partial_tool_arguments("call-a", "read_file", "{")
        tool_metadata = dict(tool_anchor.metadata)
        with pytest.raises(IdempotencyConflictError, match="identity conflict"):
            store.append_runtime_partial_batch(
                [
                    partial_event(
                        _context(),
                        {"kind": "function_call", "id": "call-b", "name": "read_file", "args": "}"},
                        {**tool_metadata, "partial_seq": 2},
                    )
                ]
            )

        assert text_key == text_metadata["partial_stream_key"]
        snapshots = store.read_runtime_stream_partials()
        assert {item.stream_kind for item in snapshots} == {"text", "function_call"}
        assert {item.payload["content"].get("text") for item in snapshots if item.stream_kind == "text"} == {"base"}


def test_partial_batch_uses_one_sqlite_transaction(tmp_path: Path):
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        recorder = _recorder(store)
        recorder.partial_text("a")
        recorder.partial_text("b")
        statements: list[str] = []
        store.connection.set_trace_callback(statements.append)

        recorder.flush_partials()

        transaction_statements = [
            statement.strip().upper()
            for statement in statements
            if statement.strip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK"))
        ]
        assert transaction_statements.count("BEGIN IMMEDIATE") == 1
        assert transaction_statements.count("COMMIT") == 1
        assert "ROLLBACK" not in transaction_statements


def test_partial_batch_rejects_mixed_invocation_identity(tmp_path: Path):
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        recorder = _recorder(store)
        first = recorder.partial_text("a")
        foreign = RuntimeEvent.create(
            RunContext("other-session", "other-turn", "other-run", "other-invocation"),
            role="model",
            author="agent",
            partial=True,
            content={"kind": "text", "text": "foreign"},
            metadata={
                "lifecycle": "stream_partial",
                "partial_stream_key": "partial:other-invocation:attempt:text:",
                "partial_seq": 1,
            },
        )

        with pytest.raises(StoreValidationError, match="one session, run and invocation"):
            store.append_runtime_partial_batch([first, foreign])
        assert store.read_runtime_stream_partials()[0].payload["content"]["text"] == "a"


def test_partial_batch_failure_preserves_snapshot_and_buffer_for_retry(tmp_path: Path):
    class CommitFault:
        enabled = False

        def check(self, point: str) -> None:
            if self.enabled and point == "store.commit":
                raise RuntimeError("partial commit failed")

    fault = CommitFault()
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite", fault_hook=fault) as store:
        recorder = _recorder(store)
        recorder.partial_text("a")
        recorder.partial_text("b")
        fault.enabled = True
        with pytest.raises(StoreFaultError, match="partial commit failed"):
            recorder.flush_partials()
        assert store.read_runtime_stream_partials()[0].payload["content"]["text"] == "a"
        fault.enabled = False
        recorder.flush_partials()
        assert store.read_runtime_stream_partials()[0].payload["content"]["text"] == "ab"


def test_final_cleanup_failure_does_not_publish_final_or_drop_snapshot(tmp_path: Path):
    class CommitFault:
        enabled = False

        def check(self, point: str) -> None:
            if self.enabled and point == "store.commit":
                raise RuntimeError("final commit failed")

    fault = CommitFault()
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite", fault_hook=fault) as store:
        recorder = _recorder(store)
        recorder.partial_text("a")
        recorder.flush_partials()
        fault.enabled = True
        with pytest.raises(StoreFaultError, match="final commit failed"):
            recorder.final_text("a")
        assert store.read_runtime_stream_partials()
        assert len(store.read_events()) == 1
        assert recorder._finished is False
        fault.enabled = False
        recorder.final_text("a")
        assert store.read_runtime_stream_partials() == []


def test_budget_failure_does_not_mark_recorder_finished():
    class BudgetFault:
        enabled = True

        def check(self, point: str, event: RuntimeEvent) -> None:
            if self.enabled and point == "emit" and event.content:
                if event.content.get("code") == "budget_exceeded":
                    raise RuntimeError("budget persistence failed")

    fault = BudgetFault()
    sink = RecordingEventSink(failure_hook=fault.check)
    recorder = _recorder(sink)
    with pytest.raises(CanonicalSinkError, match="budget persistence failed"):
        recorder.budget_exceeded("limit")
    assert recorder._finished is False

    fault.enabled = False
    recorder.budget_exceeded("limit")
    assert recorder._finished is True


class _NoneReturningStreamingSink:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []
        self.partial_batches: list[list[RuntimeEvent]] = []
        self.final_events: list[RuntimeEvent] = []

    def emit(self, event: RuntimeEvent) -> RuntimeEvent:
        self.events.append(event)
        return event

    def append_runtime_partial_batch(self, events):
        self.partial_batches.append(list(events))
        return None

    def append_event_and_clear_runtime_partials(self, event, **_kwargs):
        self.final_events.append(event)
        return None

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_streaming_extension_none_return_is_treated_as_success():
    sink = _NoneReturningStreamingSink()
    recorder = ModelCallRecorder(
        RuntimeEventEmitter(sink),
        _context(),
        provider="fixture",
        model="fixture-model",
        clock=lambda: 1_700_000_000_000,
    )
    recorder.start("stream-request")
    recorder.partial_text("a")
    recorder.partial_text("b")
    recorder.final_text("ab")

    assert recorder.emitter.partial_batch_supported is True
    assert len(sink.partial_batches) == 2
    assert [event.content["text"] for event in sink.partial_batches[0]] == ["a"]
    assert [event.content["text"] for event in sink.partial_batches[1]] == ["b"]
    assert len(sink.final_events) == 1
    assert [event.content.get("kind") for event in sink.events] == ["invocation_opened"]


def test_reopened_pending_partial_is_recovery_evidence_not_model_history(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        recorder = _recorder(store)
        recorder.partial_text("unfinished")
        recorder.flush_partials()

    with SQLiteRuntimeStore(database) as reopened:
        snapshots = reopened.read_runtime_stream_partials(run_id="stream-run")
        assert snapshots[0].payload["content"]["text"] == "unfinished"
        recovery = RecoveryProjection().scan(reopened)
        assert recovery[0].status == "open"
        assert any(item.code == "stream_partial_pending" for item in recovery[0].diagnostics)
        assert all(not event.partial for event in reopened.read_events())


def test_stream_snapshot_integrity_failure_is_fail_closed(tmp_path: Path):
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        recorder = _recorder(store)
        recorder.partial_text("safe")
        store.connection.execute(
            "UPDATE runtime_stream_partials SET payload_json = ?",
            (b"{\"tampered\":true}",),
        )

        with pytest.raises(CorruptionError, match="runtime_stream_partials"):
            store.read_runtime_stream_partials()


def test_stream_snapshot_schema_migrates_existing_rows_without_event_digest(
    tmp_path: Path,
):
    database = tmp_path / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        recorder = _recorder(store)
        recorder.partial_text("legacy")
        connection = store.connection
        connection.execute("DROP INDEX IF EXISTS idx_runtime_stream_partials_run")
        connection.execute("DROP INDEX IF EXISTS idx_runtime_stream_partials_session")
        connection.execute(
            "ALTER TABLE runtime_stream_partials RENAME TO runtime_stream_partials_legacy"
        )
        connection.execute(
            """
            CREATE TABLE runtime_stream_partials (
                stream_key TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                invocation_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                stream_kind TEXT NOT NULL,
                tool_call_id TEXT,
                tool_name TEXT,
                payload_json BLOB NOT NULL,
                first_event_id TEXT NOT NULL,
                last_event_id TEXT NOT NULL,
                first_ts INTEGER NOT NULL,
                last_ts INTEGER NOT NULL,
                fragment_count INTEGER NOT NULL,
                last_partial_seq INTEGER NOT NULL DEFAULT 0,
                size_bytes INTEGER NOT NULL,
                digest TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO runtime_stream_partials(
                stream_key, session_id, run_id, invocation_id, attempt_id,
                stream_kind, tool_call_id, tool_name, payload_json,
                first_event_id, last_event_id, first_ts, last_ts,
                fragment_count, last_partial_seq, size_bytes, digest,
                created_at, updated_at
            )
            SELECT stream_key, session_id, run_id, invocation_id, attempt_id,
                stream_kind, tool_call_id, tool_name, payload_json,
                first_event_id, last_event_id, first_ts, last_ts,
                fragment_count, last_partial_seq, size_bytes, digest,
                created_at, updated_at
            FROM runtime_stream_partials_legacy
            """
        )
        connection.execute("DROP TABLE runtime_stream_partials_legacy")
        connection.execute(
            "CREATE INDEX idx_runtime_stream_partials_run "
            "ON runtime_stream_partials(run_id, invocation_id, updated_at)"
        )
        connection.execute(
            "CREATE INDEX idx_runtime_stream_partials_session "
            "ON runtime_stream_partials(session_id, updated_at)"
        )
        connection.execute("PRAGMA user_version = 4")
        connection.commit()

    with SQLiteRuntimeStore(database) as reopened:
        columns = {
            str(row["name"])
            for row in reopened.connection.execute(
                "PRAGMA table_info(runtime_stream_partials)"
            ).fetchall()
        }
        assert "last_event_digest" in columns
        assert reopened.read_runtime_stream_partials()[0].payload["content"]["text"] == "legacy"


def test_true_v4_database_migration_creates_stream_table_and_preserves_legacy_data(
    tmp_path: Path,
):
    database = tmp_path / "runtime-v4.sqlite"
    context = _context()
    opening = RuntimeEvent.create(
        context,
        role="system",
        author="agent",
        status="streaming",
        ts=1_700_000_000_000,
        event_id="v4-opening",
        content={
            "kind": "invocation_opened",
            "protocol": "invocation_opened_v1",
            "route": {"provider": "fixture", "model": "fixture-model"},
            "configuration": {"attempt": 1},
            "root": {"kind": "agent"},
            "source": {"kind": "fresh"},
        },
    )
    event_encoded = canonical_json_bytes(opening.to_dict())
    snapshot = {"messages": [{"role": "user", "content": "legacy context"}]}
    snapshot_encoded = canonical_json_bytes(snapshot)
    created_at = "2026-09-09T00:00:00Z"

    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE runtime_events (
                event_id TEXT PRIMARY KEY,
                ordinal INTEGER NOT NULL UNIQUE,
                event_seq INTEGER NOT NULL,
                schema_version INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                invocation_id TEXT NOT NULL,
                context_id TEXT,
                parent_context_id TEXT,
                parent_run_id TEXT,
                ts INTEGER NOT NULL,
                partial INTEGER NOT NULL,
                terminal INTEGER NOT NULL,
                digest TEXT NOT NULL,
                event_json BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE runtime_run_state (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                invocation_id TEXT NOT NULL,
                parent_run_id TEXT,
                status TEXT NOT NULL,
                sealed INTEGER NOT NULL DEFAULT 0,
                terminal_event_id TEXT,
                terminal_ordinal INTEGER,
                high_water INTEGER NOT NULL DEFAULT 0,
                last_event_seq INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE runtime_partial_snapshots (
                run_id TEXT PRIMARY KEY,
                high_water INTEGER NOT NULL,
                from_ordinal INTEGER NOT NULL,
                to_ordinal INTEGER NOT NULL,
                payload_json BLOB NOT NULL,
                digest TEXT NOT NULL,
                version INTEGER NOT NULL,
                bounded INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO runtime_events(
                event_id, ordinal, event_seq, schema_version, session_id, turn_id,
                run_id, invocation_id, context_id, parent_context_id, parent_run_id,
                ts, partial, terminal, digest, event_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opening.id,
                1,
                1,
                opening.schema_version,
                opening.session_id,
                opening.turn_id,
                opening.run_id,
                opening.invocation_id,
                opening.context_id,
                opening.parent_context_id,
                opening.parent_run_id,
                opening.ts,
                0,
                0,
                opening.digest(),
                event_encoded,
                created_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO runtime_run_state(
                run_id, session_id, invocation_id, parent_run_id, status,
                sealed, terminal_event_id, terminal_ordinal, high_water, last_event_seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                context.run_id,
                context.session_id,
                context.invocation_id,
                context.parent_run_id,
                "open",
                0,
                None,
                None,
                1,
                1,
            ),
        )
        connection.execute(
            """
            INSERT INTO runtime_partial_snapshots(
                run_id, high_water, from_ordinal, to_ordinal, payload_json,
                digest, version, bounded, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                context.run_id,
                1,
                1,
                1,
                snapshot_encoded,
                hashlib.sha256(snapshot_encoded).hexdigest(),
                1,
                0,
                created_at,
            ),
        )
        connection.execute("PRAGMA user_version = 4")
        connection.commit()
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'runtime_stream_partials'"
        ).fetchone() is None

    with SQLiteRuntimeStore(database) as reopened:
        assert reopened.connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert reopened.read_events() == [opening]
        assert reopened.read_partial_snapshot(context.run_id).payload == snapshot
        assert reopened.read_runtime_stream_partials() == []
        assert reopened.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'runtime_stream_partials'"
        ).fetchone() is not None


def test_direct_partial_append_and_old_sink_fallback_remain_compatible(tmp_path: Path):
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        opening = RuntimeEvent.create(
            _context(),
            role="system",
            author="agent",
            status="streaming",
            content={
                "kind": "invocation_opened",
                "protocol": "invocation_opened_v1",
                "route": {"provider": "fixture", "model": "fixture-model"},
                "configuration": {"attempt": 1},
                "root": {"kind": "agent"},
                "source": {"kind": "fresh"},
            },
        )
        store.append(opening)
        direct = RuntimeEvent.create(
            _context(),
            role="model",
            author="agent",
            partial=True,
            content={"kind": "text", "text": "legacy"},
            metadata={"lifecycle": "stream_partial"},
        )
        store.append(direct)
        assert store.read_events()[-1] == direct

    sink = RecordingEventSink()
    recorder = _recorder(sink)
    recorder.partial_text("a")
    recorder.partial_text("b")
    assert [event.content["text"] for event in sink.events if event.partial] == ["a"]
    recorder.final_text("ab")
    assert [event.content["text"] for event in sink.events if event.partial] == ["a", "b"]


def test_old_sink_fallback_flushes_pending_partials_before_usage():
    sink = RecordingEventSink()
    recorder = _legacy_recorder(sink)

    recorder.partial_text("a")
    recorder.partial_text("b")
    recorder.usage({"input_tokens": 1})
    recorder.final_text("ab")

    assert recorder.emitter.partial_batch_supported is False
    assert [
        ((event.metadata or {}).get("lifecycle"), (event.content or {}).get("text"))
        for event in sink.events
    ] == [
        ("invocation_opened", None),
        ("stream_partial", "a"),
        ("stream_partial", "b"),
        ("usage", None),
        ("model_final", "ab"),
    ]


def test_old_sink_fallback_flushes_all_streams_before_final_text():
    sink = RecordingEventSink()
    recorder = _legacy_recorder(sink)

    recorder.partial_text("a")
    recorder.partial_tool_arguments("call-a", "read_file", "{")
    recorder.partial_tool_arguments("call-a", "read_file", '"path"')
    recorder.final_text("a")

    assert [
        (
            (event.metadata or {}).get("lifecycle"),
            (event.content or {}).get("kind"),
            (event.content or {}).get("text") or (event.content or {}).get("args"),
        )
        for event in sink.events
    ] == [
        ("invocation_opened", "invocation_opened", None),
        ("stream_partial", "text", "a"),
        ("tool_arguments_partial", "function_call", "{"),
        ("tool_arguments_partial", "function_call", '"path"'),
        ("model_final", "text", "a"),
    ]
