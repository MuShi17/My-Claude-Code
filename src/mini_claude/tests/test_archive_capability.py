"""Tests for bounded, scoped ArchiveRead capability behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mini_claude.archive_capability import (
    ArchiveReadScopeError,
    ToolResultArchiveCapability,
)
from mini_claude.artifact_archive import (
    ArtifactArchive,
    ArtifactIntegrityError,
    ArtifactMetadataError,
)
from mini_claude.runtime_store import SQLiteRuntimeStore


def test_artifact_archive_reads_unicode_pages_with_actual_offsets(tmp_path: Path) -> None:
    archive = ArtifactArchive(tmp_path / "artifacts")
    value = "首行\n第二行🙂\n第三行\n"
    ref = archive.archive(value, mime_type="text/plain", encoding="utf-8", scope="tool-result")

    first = archive.read_page(ref, offset=0, limit=6)
    second = archive.read_page(ref, offset=first.next_offset, limit=6)

    assert first.page == value[:6]
    assert first.next_offset == 6
    assert first.has_more is True
    assert second.page == value[6:12]
    assert second.offset == 6


def test_capability_reads_registered_legacy_ref_and_rejects_paths(tmp_path: Path) -> None:
    archive = ArtifactArchive(tmp_path / "artifacts")
    ref = archive.archive(
        "alpha\nbeta\ngamma\n",
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
    )
    capability = ToolResultArchiveCapability(archive, session_id="session-1")
    capability.register_ref(ref)

    result = capability.read(ref.ref, offset=6, limit=4)
    assert result["kind"] == "archive_page"
    assert result["page"] == "beta"
    assert result["offset"] == 6

    invalid = json.loads(capability.execute({"operation": "read", "path": str(tmp_path)}))
    assert invalid["error_type"] == "invalid_archive_read"


def test_capability_enforces_session_and_lineage_metadata(tmp_path: Path) -> None:
    archive = ArtifactArchive(tmp_path / "artifacts")
    ref = archive.archive(
        "secret content",
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
        metadata={"session_id": "session-a", "run_id": "run-a"},
    )

    allowed = ToolResultArchiveCapability(archive, session_id="session-a", run_id="run-a")
    assert allowed.inspect(ref.ref)["ref"] == ref.ref

    wrong_session = ToolResultArchiveCapability(archive, session_id="session-b", run_id="run-a")
    denied = json.loads(wrong_session.execute({"operation": "inspect", "ref": ref.ref}))
    assert denied["error_type"] == "session_mismatch"

    wrong_lineage = ToolResultArchiveCapability(archive, session_id="session-a", run_id="run-b")
    denied = json.loads(wrong_lineage.execute({"operation": "inspect", "ref": ref.ref}))
    assert denied["error_type"] == "scope_denied"


def test_capability_bounds_limits_and_reports_metadata(tmp_path: Path) -> None:
    archive = ArtifactArchive(tmp_path / "artifacts")
    ref = archive.archive(
        "x" * 32,
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
        metadata={"session_id": "session-a", "run_id": "run-a"},
    )
    capability = ToolResultArchiveCapability(archive, session_id="session-a", run_id="run-a")

    too_large = json.loads(
        capability.execute({"operation": "read", "ref": ref.ref, "limit": 7_501})
    )
    assert too_large["error_type"] == "limit_exceeded"

    inspected = capability.execute(
        {"operation": "query", "ref": ref.ref, "query": ["ref", "size_bytes"]}
    )
    value = json.loads(inspected)
    assert value["fields"] == {"ref": ref.ref, "size_bytes": 32}


def test_capability_default_and_maximum_page_sizes_are_bounded(tmp_path: Path) -> None:
    archive = ArtifactArchive(tmp_path / "artifacts")
    value = "x" * 10_000
    ref = archive.archive(value, mime_type="text/plain", encoding="utf-8", scope="tool-result")
    capability = ToolResultArchiveCapability(archive, session_id="session-a")
    capability.register_ref(ref)

    default_page = capability.read(ref.ref)
    assert len(default_page["page"]) == 6_000
    assert default_page["unit"] == "chars"
    assert default_page["next_offset"] == 6_000

    maximum_page = capability.read(ref.ref, offset=6_000, limit=7_500)
    assert len(maximum_page["page"]) == 4_000
    assert maximum_page["unit"] == "chars"
    assert maximum_page["next_offset"] == 10_000


def test_capability_fails_closed_on_integrity_and_closed_store(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite"
    store = SQLiteRuntimeStore(database)
    archive = ArtifactArchive(tmp_path / "artifacts", metadata_store=store)
    ref = archive.archive(
        "safe content",
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
        metadata={"session_id": "session-a", "run_id": "run-a"},
    )
    content_path = archive._paths(ref.digest)[0]
    content_path.write_text("tampered", encoding="utf-8")
    capability = ToolResultArchiveCapability(archive, session_id="session-a", run_id="run-a")

    with pytest.raises(ArtifactIntegrityError):
        capability.inspect(ref.ref)

    content_path.write_bytes(ref.size_bytes.to_bytes(1, "big"))
    store.close()
    closed = json.loads(capability.execute({"operation": "inspect", "ref": ref.ref}))
    assert closed["error_type"] == "archive_store_closed"


def test_derived_capability_keeps_parent_ref_grant_without_ownership_transfer(
    tmp_path: Path,
) -> None:
    archive = ArtifactArchive(tmp_path / "artifacts")
    ref = archive.archive(
        "parent content",
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
    )
    parent = ToolResultArchiveCapability(archive, session_id="session-a", run_id="parent")
    parent.register_ref(ref)
    child = parent.derive(run_id="child", parent_run_id="parent")

    assert child.inspect(ref.ref)["ref"] == ref.ref
    assert child.archive is archive
    assert parent.inspect(ref.ref)["ref"] == ref.ref


def test_projection_archive_writer_separates_logical_identity_from_content_digest(
    tmp_path: Path,
) -> None:
    archive = ArtifactArchive(tmp_path / "artifacts")
    capability = ToolResultArchiveCapability(
        archive,
        session_id="session-a",
        run_id="run-a",
        parent_run_id="parent-a",
    )
    value = {"text": "正文🙂" * 5_000}

    first = capability.archive_result(value, call_id="call-a", tool_name="read_file")
    second = capability.archive_result(value, call_id="call-b", tool_name="read_file")

    assert first.ref != second.ref
    assert first.artifact_id != second.artifact_id
    assert first.sha256 == second.sha256
    assert first.ref.startswith("artifact:tool-result:")
    retry = capability.archive_result(value, call_id="call-a", tool_name="read_file")
    assert retry.ref == first.ref
    metadata = capability.inspect(first.ref)
    assert metadata["scope"] == "tool-result"
    assert metadata["metadata"]["session_id"] == "session-a"
    assert metadata["metadata"]["run_id"] == "run-a"
    assert capability.materialize(first.ref) == value
    assert capability.read(first.ref, offset=0, limit=32)["unit"] == "bytes"

    child = capability.derive(run_id="child-a", parent_run_id="run-a")
    assert child.inspect(first.ref)["ref"] == first.ref


def test_same_canonical_tool_result_reuses_logical_ref_across_chat_runs(
    tmp_path: Path,
) -> None:
    archive = ArtifactArchive(tmp_path / "artifacts")
    first = ToolResultArchiveCapability(archive, session_id="session-a", run_id="run-a")
    second = ToolResultArchiveCapability(archive, session_id="session-a", run_id="run-b")
    value = {"same": "canonical body🙂"}
    identity = {
        "call_id": "call-a",
        "tool_name": "read_file",
        "runtime_event_id": "event-a",
    }

    first_ref = first.archive_result(value, **identity)
    second_ref = second.archive_result(value, **identity)

    assert second_ref.ref == first_ref.ref
    assert second_ref.artifact_id == first_ref.artifact_id
    assert second.inspect(second_ref.ref)["ref"] == first_ref.ref
    assert second.inspect(second_ref.ref)["metadata"]["run_id"] == "run-a"
    assert len(list((tmp_path / "artifacts" / "refs").glob("*.json"))) == 1
    assert len(list((tmp_path / "artifacts" / "sha256").rglob("*.bin"))) == 1


def test_registered_historical_ref_keeps_integrity_checks_across_chat_runs(
    tmp_path: Path,
) -> None:
    archive = ArtifactArchive(tmp_path / "artifacts")
    source = ToolResultArchiveCapability(
        archive,
        session_id="session-a",
        run_id="run-a",
    )
    later = ToolResultArchiveCapability(
        archive,
        session_id="session-a",
        run_id="run-b",
    )
    ref = source.archive_result(
        "historical body",
        call_id="call-a",
        tool_name="fixture",
        runtime_event_id="event-a",
    )

    with pytest.raises(ArchiveReadScopeError):
        later.read(ref.ref, limit=4)

    later.register_ref(ref)
    page = later.read(
        ref.ref,
        limit=4,
        expected_sha256=ref.sha256,
        expected_size_bytes=ref.size_bytes,
    )
    assert page["page"] == "hist"

    with pytest.raises(ArtifactIntegrityError):
        later.read(ref.ref, expected_sha256="0" * 64)
    with pytest.raises(ArtifactIntegrityError):
        later.read(ref.ref, expected_size_bytes=ref.size_bytes + 1)


def test_identical_tool_result_in_two_sessions_has_independent_authorization(
    tmp_path: Path,
) -> None:
    archive = ArtifactArchive(tmp_path / "artifacts")
    first = ToolResultArchiveCapability(archive, session_id="session-a", run_id="run-a")
    second = ToolResultArchiveCapability(archive, session_id="session-b", run_id="run-b")
    value = {"same": "body🙂"}

    a_ref = first.archive_result(
        value,
        call_id="call-a",
        tool_name="fixture",
        runtime_event_id="event-a",
    )
    b_ref = second.archive_result(
        value,
        call_id="call-a",
        tool_name="fixture",
        runtime_event_id="event-a",
    )

    assert a_ref.ref != b_ref.ref
    assert a_ref.sha256 == b_ref.sha256
    assert first.materialize(a_ref.ref) == value
    assert second.materialize(b_ref.ref) == value
    assert json.loads(second.execute({"operation": "inspect", "ref": a_ref.ref}))["error_type"] == "session_mismatch"
    assert json.loads(first.execute({"operation": "inspect", "ref": b_ref.ref}))["error_type"] == "session_mismatch"
    assert len(list((tmp_path / "artifacts" / "sha256").rglob("*.bin"))) == 1
    assert len(list((tmp_path / "artifacts" / "refs").glob("*.json"))) == 2
    assert archive.diagnose() == []


@pytest.mark.parametrize("fault_point", ["artifact.metadata", "store.artifact_metadata"])
def test_logical_archive_publication_rolls_back_on_metadata_failure(
    tmp_path: Path,
    fault_point: str,
) -> None:
    class Fault:
        def check(self, point: str) -> None:
            if point == fault_point:
                raise RuntimeError("injected publication failure")

    store = SQLiteRuntimeStore(tmp_path / "runtime.sqlite", fault_hook=Fault()) if fault_point.startswith("store.") else None
    archive = ArtifactArchive(
        tmp_path / "artifacts",
        metadata_store=store,
        fault_hook=Fault() if fault_point.startswith("artifact.") else None,
    )
    capability = ToolResultArchiveCapability(archive, session_id="session-a", run_id="run-a")

    with pytest.raises(ArtifactMetadataError):
        capability.archive_result("new logical body", call_id="call-a", tool_name="fixture")

    assert not list((tmp_path / "artifacts").rglob("*.bin"))
    assert not list((tmp_path / "artifacts" / "refs").glob("*.json"))
    if store is not None:
        assert store.list_artifact_metadata() == []
        store.close()


def test_logical_metadata_failure_does_not_delete_shared_content_blob(tmp_path: Path) -> None:
    class Fault:
        def check(self, point: str) -> None:
            if point == "store.artifact_metadata":
                raise RuntimeError("injected mirror failure")

    store = SQLiteRuntimeStore(tmp_path / "runtime.sqlite")
    archive = ArtifactArchive(tmp_path / "artifacts", metadata_store=store)
    capability = ToolResultArchiveCapability(archive, session_id="session-a", run_id="run-a")
    first = capability.archive_result("shared body", call_id="call-a", tool_name="fixture")
    store.fault_hook = Fault()

    with pytest.raises(ArtifactMetadataError):
        capability.archive_result("shared body", call_id="call-b", tool_name="fixture")

    assert list((tmp_path / "artifacts" / "sha256").rglob("*.bin"))
    assert len(list((tmp_path / "artifacts" / "refs").glob("*.json"))) == 1
    assert capability.materialize(first.ref) == "shared body"
    store.close()


def test_logical_metadata_digest_or_size_mismatch_fails_closed(tmp_path: Path) -> None:
    archive = ArtifactArchive(tmp_path / "artifacts")
    capability = ToolResultArchiveCapability(archive, session_id="session-a", run_id="run-a")
    ref = capability.archive_result("immutable body", call_id="call-a", tool_name="fixture")
    metadata_path = archive._logical_metadata_path(ref.artifact_id or "")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["size_bytes"] += 1
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError):
        capability.inspect(ref.ref)


def test_logical_archive_rejects_conflicting_scope_metadata(tmp_path: Path) -> None:
    archive = ArtifactArchive(tmp_path / "artifacts")
    capability = ToolResultArchiveCapability(archive, session_id="session-a", run_id="run-a")

    with pytest.raises(ValueError, match="session_id"):
        capability.archive_result(
            "body",
            call_id="call-a",
            tool_name="fixture",
            metadata={"session_id": "session-b"},
        )

    assert not list((tmp_path / "artifacts").rglob("*.bin"))
