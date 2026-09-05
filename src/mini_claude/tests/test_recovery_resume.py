"""C10 canonical-first recovery, resume and non-destructive migration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import mini_claude.session as session_module
from mini_claude.artifact_archive import ArtifactArchive
from mini_claude.compaction import CompactionCheckpointBuilder
from mini_claude.recovery import RecoveryProjection, classify_legacy_only
from mini_claude.runtime_event import RuntimeEvent
from mini_claude.runtime_store import SQLiteRuntimeStore
from mini_claude.session import (
    CanonicalRecoveryError,
    build_session_v2,
    list_sessions,
    list_runtime_store_paths,
    load_session,
    runtime_store_path,
    save_session,
    save_session_v2,
)

from runtime_fixtures import build_scenario, scenario_events


def _events() -> list[RuntimeEvent]:
    return [RuntimeEvent.from_dict(item) for item in scenario_events(build_scenario())]


def _terminal(event: RuntimeEvent, *, event_id: str = "terminal-c10") -> RuntimeEvent:
    data = event.to_dict()
    data.update(
        {
            "id": event_id,
            "status": "completed",
            "content": {"kind": "text", "text": "done"},
            "actions": {"end_run": True},
        }
    )
    return RuntimeEvent.from_dict(data)


def test_recovery_classifies_terminal_open_partial_and_uncertain(tmp_path: Path):
    events = _events()
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        store.append(_terminal(events[0]))
        terminal = RecoveryProjection().scan(store)
        assert terminal[0].status == "terminal"

    with SQLiteRuntimeStore(tmp_path / "open.sqlite") as store:
        store.append(events[0])
        assert RecoveryProjection().scan(store)[0].status == "open"
        store.append(RuntimeEvent.from_dict({**events[1].to_dict(), "id": "partial-c10", "partial": True}))
        assert RecoveryProjection().scan(store)[0].status == "open"

    with SQLiteRuntimeStore(tmp_path / "partial.sqlite") as store:
        data = events[1].to_dict()
        data.update({"id": "partial-only-c10", "partial": True})
        store.append(RuntimeEvent.from_dict(data))
        assert RecoveryProjection().scan(store)[0].status == "partial-only"

    with SQLiteRuntimeStore(tmp_path / "uncertain.sqlite") as store:
        for event in events[:5]:
            store.append(event)
        result = RecoveryProjection().scan(store)[0]
        assert result.status == "uncertain"
        assert result.uncertain_call_ids
        assert any(item.code == "uncertain_tool_dispatch" for item in result.diagnostics)


def test_startup_closure_is_idempotent_and_never_retries_uncertain_tool(tmp_path: Path):
    events = _events()
    with SQLiteRuntimeStore(tmp_path / "open.sqlite") as store:
        store.append(events[0])
        recovery = RecoveryProjection()
        first = recovery.recover_startup(store)
        assert first[0].status == "terminal"
        terminal_count = len([event for event in store.read_events() if event.is_terminal])
        second = recovery.recover_startup(store)
        assert second[0].status == "terminal"
        assert len([event for event in store.read_events() if event.is_terminal]) == terminal_count == 1

    with SQLiteRuntimeStore(tmp_path / "uncertain.sqlite") as store:
        for event in events[:5]:
            store.append(event)
        recovery.recover_startup(store)
        assert not any(event.is_terminal for event in store.read_events())


def test_corrupt_event_and_artifact_ref_are_diagnostic_only(tmp_path: Path):
    events = _events()
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        store.append(events[0])
        store.connection.execute(
            "UPDATE runtime_events SET event_json = ? WHERE event_id = ?",
            (b"{bad", events[0].id),
        )
        result = RecoveryProjection().scan(store)[0]
        assert result.status == "corrupt"
        assert any(item.code == "corrupt_event" for item in result.diagnostics)

    archive = ArtifactArchive(tmp_path / "artifacts")
    placeholder = {
        "kind": "bounded_ref", "ref": "artifact:sha256:" + "b" * 64,
        "sha256": "sha256:" + "b" * 64, "size_bytes": 10, "mime_type": "text/plain",
        "encoding": "utf-8", "scope": "tool-result", "redaction_version": "redaction-v1",
    }
    data = events[1].to_dict()
    data["content"] = {"kind": "text", "text": "safe"}
    data["refs"] = {"artifact_ref": placeholder["ref"]}
    data["metadata"] = {"artifact": placeholder}
    # Runtime ref integrity is diagnosed by recovery when a bounded_ref occurs.
    with SQLiteRuntimeStore(tmp_path / "ref.sqlite") as store:
        store.append(RuntimeEvent.from_dict(data))
        result = RecoveryProjection(artifact_archive=archive).scan(store)[0]
        assert any(item.code == "artifact_not_found" for item in result.diagnostics)


def test_session_v2_is_canonical_derived_and_stale_snapshot_is_not_authority(tmp_path: Path, monkeypatch):
    events = _events()
    database = tmp_path / "runtime.sqlite"
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path / "sessions")
    with SQLiteRuntimeStore(database) as store:
        for event in events[:2]:
            store.append(event)
        first = save_session_v2(events[0].session_id, store)
        assert first is not None
        assert first["schemaVersion"] == 2
        assert first["metadata"]["source"] == "canonical"
        assert first["metadata"]["sourceDigest"]
        assert first["coverage"]["toOrdinal"] == 2
        store.append(events[2])
        current = build_session_v2(events[0].session_id, store)
        assert current["metadata"]["highWater"] == 3
        assert current["metadata"]["sourceDigest"] != first["metadata"]["sourceDigest"]
        loaded = load_session(events[0].session_id, runtime_store=store, canonical_first=True)
        assert loaded["metadata"]["highWater"] == 3
        assert load_session(events[0].session_id)["metadata"]["source"] == "canonical"


def test_v2_atomic_write_preserves_old_snapshot_on_migration_failure(tmp_path: Path, monkeypatch):
    events = _events()
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path / "sessions")
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        store.append(events[0])
        old = save_session_v2(events[0].session_id, store)
        old_bytes = (tmp_path / "sessions" / events[0].session_id / "session.v2.json").read_bytes()

        def fail(*args, **kwargs):
            raise OSError("snapshot fault")

        monkeypatch.setattr(session_module, "_atomic_write_json", fail)
        with pytest.raises(OSError, match="snapshot fault"):
            save_session_v2(events[0].session_id, store)
        assert (tmp_path / "sessions" / events[0].session_id / "session.v2.json").read_bytes() == old_bytes
        assert store.read_events() == [events[0]]


def test_legacy_fallback_is_explicit_readonly_and_has_no_fabricated_dispatch(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path / "sessions")
    save_session("legacy-session", {
        "metadata": {"id": "legacy-session"},
        "anthropicMessages": [{"role": "user", "content": "old"}],
    })
    loaded = load_session("legacy-session")
    assert loaded["metadata"]["source"] == "legacy-readonly"
    assert loaded["metadata"]["readonly"] is True
    classified = classify_legacy_only(loaded)
    assert classified["status"] == "legacy-only"
    assert classified["fabricated_dispatch"] is False
    assert list_sessions()[0]["source"] == "legacy-readonly"


def test_checkpoint_source_mismatch_is_safe_diagnostic(tmp_path: Path):
    events = _events()
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        for event in events[:2]:
            store.append(event)
        checkpoint = CompactionCheckpointBuilder().build(store, high_water=2)
        store.append(events[2])
        diagnostic = RecoveryProjection().verify_checkpoint(store, checkpoint)
        assert diagnostic is None  # the checkpoint deliberately targets H=2
        wrong = dict(checkpoint.to_dict())
        wrong["source_digest"] = "sha256:" + "0" * 64
        diagnostic = RecoveryProjection().verify_checkpoint(store, wrong)
        assert diagnostic is not None and diagnostic.code == "checkpoint_source_mismatch"


def test_canonical_corruption_is_raised_and_database_is_retained(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path / "sessions")
    database = runtime_store_path("corrupt-session")
    events = _events()
    with SQLiteRuntimeStore(database) as store:
        store.append(events[0])
        store.connection.execute(
            "UPDATE runtime_events SET event_json = ? WHERE event_id = ?",
            (b"{broken", events[0].id),
        )
    with SQLiteRuntimeStore(database) as store:
        with pytest.raises(CanonicalRecoveryError) as error:
            load_session("corrupt-session", runtime_store=store, canonical_first=True)
    assert error.value.classification == "corrupt"
    assert database.exists()
    with SQLiteRuntimeStore(database) as store:
        raw = store.connection.execute(
            "SELECT event_json FROM runtime_events WHERE event_id = ?", (events[0].id,)
        ).fetchone()[0]
    assert raw == b"{broken"


def test_runtime_store_paths_are_session_isolated(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path / "sessions")
    events = _events()
    event_one = events[0]
    event_two = RuntimeEvent.from_dict({
        **events[1].to_dict(),
        "id": "session-two-event",
        "session_id": "session-two",
        "run_id": "run-session-two",
        "turn_id": "turn-session-two",
        "invocation_id": "invocation-session-two",
    })
    with SQLiteRuntimeStore(runtime_store_path("session-one")) as first:
        first.append(RuntimeEvent.from_dict({
            **event_one.to_dict(), "session_id": "session-one", "run_id": "run-session-one",
            "turn_id": "turn-session-one", "invocation_id": "invocation-session-one",
        }))
    with SQLiteRuntimeStore(runtime_store_path("session-two")) as second:
        second.append(event_two)

    paths = list_runtime_store_paths()
    assert runtime_store_path("session-one") in paths
    assert runtime_store_path("session-two") in paths
    with SQLiteRuntimeStore(runtime_store_path("session-one")) as first:
        assert {event.session_id for event in first.read_events()} == {"session-one"}
    with SQLiteRuntimeStore(runtime_store_path("session-two")) as second:
        assert {event.session_id for event in second.read_events()} == {"session-two"}
