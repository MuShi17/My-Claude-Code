"""C05 SQLite runtime-store invariants."""

from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from mini_claude.runtime_event import RuntimeEvent
from mini_claude.runtime_store import (
    CorruptionError,
    IdempotencyConflictError,
    SealedRunError,
    SQLiteRuntimeStore,
)

from runtime_fixtures import build_scenario, scenario_events


def _events() -> list[RuntimeEvent]:
    return [RuntimeEvent.from_dict(item) for item in scenario_events(build_scenario())]


def test_store_creates_schema_reopens_and_replays_exactly(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    events = _events()
    with SQLiteRuntimeStore(database) as store:
        first = store.append(events[0])
        second = store.append(events[0])
        assert first.ordinal == 1
        assert second.ordinal == first.ordinal
        assert second.idempotent is True
        assert store.read_events() == [events[0]]
        tables = {
            row[0]
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "runtime_events",
            "runtime_session_event_ordinals",
            "runtime_run_state",
            "runtime_partial_snapshots",
            "runtime_llm_captures",
        } <= tables
    with SQLiteRuntimeStore(database) as reopened:
        assert reopened.read_events() == [events[0]]
        assert reopened.current_high_water == 1


def test_store_uses_ordinal_not_timestamp_and_keeps_child_filter_separate(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    events = _events()
    with SQLiteRuntimeStore(database) as store:
        results = [store.append(event) for event in events[:3]]
        assert [item.ordinal for item in results] == [1, 2, 3]
        assert [item.event_seq for item in results] == [1, 2, 3]
        assert store.run_state(events[0].run_id).last_event_seq == 3
        prefix = store.read_immutable_prefix(session_id=events[0].session_id, high_water=2)
        assert prefix.high_water == 2
        assert list(prefix.events) == events[:2]
        child_data = dict(events[2].to_dict())
        child_data["id"] = "child-event"
        child_data["run_id"] = "child-run"
        child_data["parent_run_id"] = events[0].run_id
        child = RuntimeEvent.from_dict(child_data)
        result = store.append(child)
        assert result.ordinal == 4
        assert result.event_seq == 4
        assert store.read_events(run_id="child-run") == [child]


def test_store_assigns_invocation_sequence_under_concurrent_writers(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    events = _events()
    with SQLiteRuntimeStore(database) as store:
        store.append(events[0])

    def append_one(index: int):
        payload = events[1].to_dict()
        payload["id"] = f"concurrent-event-{index}"
        payload["content"] = {"kind": "text", "text": f"event-{index}"}
        event = RuntimeEvent.from_dict(payload)
        with SQLiteRuntimeStore(database, timeout=5) as store:
            return store.append(event)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(append_one, range(8)))

    assert sorted(result.event_seq for result in results) == list(range(2, 10))
    assert len({result.ordinal for result in results}) == 8
    with SQLiteRuntimeStore(database) as store:
        assert store.run_state(events[0].run_id).last_event_seq == 9


def test_store_conflicting_payload_and_terminal_seal_are_explicit(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    event = _events()[1]
    with SQLiteRuntimeStore(database) as store:
        store.append(_events()[0])
        store.append(event)
        conflict = dict(event.to_dict())
        conflict["content"] = {"kind": "text", "text": "different"}
        with pytest.raises(IdempotencyConflictError):
            store.append(RuntimeEvent.from_dict(conflict))

        terminal_data = dict(event.to_dict())
        terminal_data["id"] = "terminal-event"
        terminal_data["status"] = "completed"
        terminal = RuntimeEvent.from_dict(terminal_data)
        sealed = store.seal_run(terminal)
        assert sealed.ordinal == 3
        assert store.run_state(event.run_id).sealed is True
        assert store.append(terminal).idempotent is True

        after = dict(event.to_dict())
        after["id"] = "after-terminal"
        with pytest.raises(SealedRunError):
            store.append(RuntimeEvent.from_dict(after))


def test_tool_outcome_cannot_cross_run_or_be_recorded_twice(tmp_path: Path):
    events = _events()
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        store.append(events[0])
        store.append(events[4])
        store.append(events[5])

        duplicate = events[5].to_dict()
        duplicate["id"] = "duplicate-tool-outcome"
        with pytest.raises(IdempotencyConflictError, match="already recorded"):
            store.append(RuntimeEvent.from_dict(duplicate))

        foreign_opening = events[0].to_dict()
        foreign_opening.update(
            {
                "id": "foreign-opening",
                "turn_id": "foreign-turn",
                "run_id": "foreign-run",
                "invocation_id": "foreign-invocation",
            }
        )
        store.append(RuntimeEvent.from_dict(foreign_opening))
        foreign_outcome = events[5].to_dict()
        foreign_outcome.update(
            {
                "id": "foreign-outcome",
                "turn_id": "foreign-turn",
                "run_id": "foreign-run",
                "invocation_id": "foreign-invocation",
            }
        )
        with pytest.raises(IdempotencyConflictError, match="identity conflict"):
            store.append(RuntimeEvent.from_dict(foreign_outcome))


def test_partial_snapshot_is_bounded_and_recoverable(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    event = _events()[0]
    with SQLiteRuntimeStore(database, max_snapshot_bytes=10) as store:
        store.append(event)
        snapshot = store.write_partial_snapshot(
            event.run_id,
            {"text": "x" * 100},
            high_water=1,
            from_ordinal=1,
            to_ordinal=1,
        )
        assert snapshot.bounded is True
        restored = store.read_partial_snapshot(event.run_id)
        assert restored is not None
        assert restored.high_water == 1
        assert restored.payload["kind"] == "bounded_ref"


def test_malformed_row_is_not_silently_projected(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        event = _events()[0]
        store.append(event)
        store.connection.execute(
            "UPDATE runtime_events SET event_json = ? WHERE event_id = ?",
            (b"{not-json", event.id),
        )
        with pytest.raises(CorruptionError, match=event.id):
            store.read_events()


def test_event_sequence_gap_is_not_silently_projected(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    events = _events()
    with SQLiteRuntimeStore(database) as store:
        for event in events[:3]:
            store.append(event)
        store.connection.execute(
            "DELETE FROM runtime_events WHERE event_id = ?", (events[1].id,)
        )
        with pytest.raises(CorruptionError, match="event sequence gap"):
            store.read_events()
