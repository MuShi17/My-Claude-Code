"""Tests for bounded, scoped ArchiveRead capability behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mini_claude.archive_capability import (
    ToolResultArchiveCapability,
)
from mini_claude.artifact_archive import (
    ArtifactArchive,
    ArtifactIntegrityError,
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

