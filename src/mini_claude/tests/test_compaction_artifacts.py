"""C09 compaction, artifact archive, capture and privacy gates."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mini_claude.artifact_archive import (
    ArtifactArchive,
    ArtifactIntegrityError,
    ArtifactSizeLimitError,
    LegacyToolResultsAdapter,
)
from mini_claude.compaction import (
    CheckpointSourceMismatchError,
    CompactionCheckpointBuilder,
)
from mini_claude.event_ids import RunContext
from mini_claude.event_sink import RecordingEventSink, RuntimeEventEmitter
from mini_claude.llm_capture import LLMCaptureManager, LLMCapturePolicy
from mini_claude.projections import ModelReplayProjection
from mini_claude.redaction import RedactionPolicy
from mini_claude.runtime_event import RuntimeEvent
from mini_claude.runtime_lifecycle import DurableToolBoundary
from mini_claude.runtime_store import SQLiteRuntimeStore

from runtime_fixtures import FaultInjector, build_scenario, scenario_events


def _events() -> list[RuntimeEvent]:
    return [RuntimeEvent.from_dict(item) for item in scenario_events(build_scenario())]


def _context() -> RunContext:
    return RunContext(
        session_id="session-c09",
        turn_id="turn-c09",
        run_id="run-c09",
        invocation_id="invocation-c09",
    )


def test_checkpoint_has_prefix_digest_coverage_and_stable_rebuild(tmp_path: Path):
    database = tmp_path / "runtime.sqlite"
    events = _events()
    with SQLiteRuntimeStore(database) as store:
        for event in events[:3]:
            store.append(event)
        builder = CompactionCheckpointBuilder(recent_tail_events=2)
        checkpoint = builder.build(store, high_water=3)
        rebuilt = builder.rebuild(checkpoint, store)
        assert rebuilt.digest() == checkpoint.digest()
        assert rebuilt.source_high_water == 3
        assert rebuilt.source_digest == checkpoint.source_digest
        assert rebuilt.coverage["to_ordinal"] == 3
        assert len(rebuilt.recent_tail) == 2
        store.write_compaction_checkpoint(checkpoint)
        assert store.read_compaction_checkpoint(checkpoint.checkpoint_id) == checkpoint.to_dict()

        store.append(events[3])
        builder.verify(checkpoint, store)  # H=3 remains an immutable prefix.
        later = builder.build(store, high_water=4)
        assert later.source_digest != checkpoint.source_digest


def test_checkpoint_rejects_claimed_unread_high_water():
    with pytest.raises(Exception):
        CompactionCheckpointBuilder().build(_events()[:2], high_water=3)


def test_artifact_archive_is_redacted_atomic_content_addressed_and_bounded(tmp_path: Path):
    archive = ArtifactArchive(tmp_path / "artifacts")
    value = "password=sk-ant-super-secret\n" + ("alpha\n" * 5000)
    first = archive.archive(
        value,
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
        redaction_policy=RedactionPolicy(max_inline_bytes=32),
    )
    second = archive.archive(
        value,
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
        redaction_policy=RedactionPolicy(max_inline_bytes=32),
    )
    assert first.ref == second.ref
    assert first.size_bytes > 32
    assert "sk-ant-super-secret" not in archive.read(first, preview=True, max_bytes=512)
    with pytest.raises(ArtifactSizeLimitError):
        archive.read(first, max_bytes=32)
    assert archive.inspect(first).sha256 == first.sha256


@pytest.mark.parametrize("fault_point", ["artifact.write", "artifact.fsync", "artifact.metadata"])
def test_archive_fault_does_not_return_a_reference(tmp_path: Path, fault_point: str):
    archive = ArtifactArchive(tmp_path / "artifacts", fault_hook=FaultInjector(fault_point))
    with pytest.raises(RuntimeError, match=f"fixture fault at {fault_point}"):
        archive.archive("x" * 100)
    assert list((tmp_path / "artifacts").rglob("*.json")) == []


def test_durable_tool_archives_before_emitting_bounded_outcome(tmp_path: Path):
    sink = RecordingEventSink()
    archive = ArtifactArchive(tmp_path / "artifacts")
    boundary = DurableToolBoundary(
        RuntimeEventEmitter(sink),
        _context(),
        max_result_bytes=32,
        artifact_archive=archive,
    )
    result = asyncio.run(boundary.execute(
        call_id="call-large",
        name="read_file",
        arguments={"file_path": "sample.txt"},
        permission="allow",
        executor=lambda: "line\n" * 1000,
    ))
    assert result.success is True
    assert result.result["kind"] == "bounded_ref"
    assert archive.inspect(result.result["ref"]).size_bytes == result.result["size_bytes"]
    outcomes = [event for event in sink.events if event.kind == "tool_outcome"]
    assert outcomes[-1].content["result"]["ref"] == result.result["ref"]


def test_durable_tool_archive_failure_is_bounded_and_has_no_dangling_ref(tmp_path: Path):
    sink = RecordingEventSink()
    archive = ArtifactArchive(tmp_path / "artifacts", fault_hook=FaultInjector("artifact.write"))
    boundary = DurableToolBoundary(
        RuntimeEventEmitter(sink), _context(), max_result_bytes=32, artifact_archive=archive
    )
    result = asyncio.run(boundary.execute(
        call_id="call-fault",
        name="read_file",
        arguments={},
        permission="allow",
        executor=lambda: "x" * 1000,
    ))
    assert result.success is False
    assert result.result["kind"] == "archive_error"
    assert "ref" not in result.result


def test_llm_capture_modes_and_redacted_body_bound(tmp_path: Path):
    secret_request = {"messages": [{"role": "user", "content": "password=sk-ant-secret"}]}
    response = {"choices": [{"message": {"content": "ok"}}]}
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        off = LLMCaptureManager(
            policy=LLMCapturePolicy(mode="off"), runtime_store=store
        ).capture(
            request_id="req-off", session_id="session-c09", provider="anthropic", model="m",
            request=secret_request, response=response,
        )
        metadata = store.read_llm_capture(off.llm_ref) if hasattr(store, "read_llm_capture") else None
        assert off.capture_status == "off"
        assert off.metadata["body_present"] is False
        assert metadata is not None and "sk-ant-secret" not in json.dumps(metadata)

        only = LLMCaptureManager(
            policy=LLMCapturePolicy(mode="metadata-only"), runtime_store=store
        ).capture(
            request_id="req-meta", session_id="session-c09", provider="openai", model="m",
            request=secret_request, response=response, latency_ms=12,
        )
        assert only.capture_status == "metadata-only"
        assert only.metadata["request_shape_hash"].startswith("sha256:")

        redacted = LLMCaptureManager(
            policy=LLMCapturePolicy(mode="redacted", max_body_bytes=64, archive_bodies=False),
            runtime_store=store,
        ).capture(
            request_id="req-redacted", session_id="session-c09", provider="anthropic", model="m",
            request=secret_request, response={"text": "z" * 1000},
        )
        assert redacted.capture_status == "saved"
        assert redacted.metadata["body_present"] is True
        assert "sk-ant-secret" not in json.dumps(redacted.to_dict())
        assert redacted.metadata["response_truncated"] is True


def test_projection_keeps_artifact_ref_bounded_and_does_not_write_store(tmp_path: Path):
    events = _events()
    call_id = events[2].content["id"]
    placeholder = {
        "kind": "bounded_ref", "ref": "artifact:sha256:" + "a" * 64,
        "sha256": "sha256:" + "a" * 64, "size_bytes": 999999, "mime_type": "text/plain",
        "encoding": "utf-8", "scope": "tool-result", "redaction_version": "redaction-v1",
    }
    data = events[-1].to_dict()
    data["content"] = {"kind": "function_response", "id": call_id, "name": "read_file", "result": placeholder}
    data["refs"] = {"tool_call_id": call_id}
    data["metadata"] = {"lifecycle": "function_response"}
    ref_event = RuntimeEvent.from_dict(data)
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        store.append(events[2])
        store.append(ref_event)
        before = store.current_high_water
        replay = ModelReplayProjection().build(store)
        assert before == store.current_high_water
        assert replay.messages[-1]["content"]["kind"] == "bounded_ref"


def test_diagnostics_detect_orphan_and_hash_mismatch_without_canonical_rewrite(tmp_path: Path):
    archive = ArtifactArchive(tmp_path / "artifacts")
    ref = archive.archive("payload", mime_type="text/plain", encoding="utf-8")
    content_path = next((tmp_path / "artifacts").rglob(f"{ref.digest}.bin"))
    metadata_path = next((tmp_path / "artifacts").rglob(f"{ref.digest}.json"))
    content_path.write_bytes(b"tampered")
    assert any(item.code == "artifact_integrity_error" for item in archive.diagnose())
    metadata_path.unlink()
    assert any(item.code == "orphan_archive" for item in archive.diagnose())

    events = _events()
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        for event in events[:2]:
            store.append(event)
        before = store.current_high_water
        checkpoint = CompactionCheckpointBuilder().build(store, high_water=2)
        store.append(events[2])
        with pytest.raises(CheckpointSourceMismatchError):
            CompactionCheckpointBuilder().verify(checkpoint, [events[0], events[2]])
        assert store.current_high_water == before + 1


def test_legacy_tool_results_are_read_only_and_do_not_overlap_new_archive(tmp_path: Path):
    legacy = tmp_path / "tool-results"
    legacy.mkdir()
    old = legacy / "old.txt"
    old.write_text("old result", encoding="utf-8")
    adapter = LegacyToolResultsAdapter(legacy)
    assert adapter.read(old) == "old result"
    archive = ArtifactArchive(tmp_path / "artifacts")
    new = archive.archive("new result", mime_type="text/plain", encoding="utf-8")
    assert new.ref not in str(old)
    assert [item.name for item in adapter.list()] == ["old.txt"]
