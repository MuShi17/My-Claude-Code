"""Canonical-only acceptance gates for the runtime event system."""

from __future__ import annotations

import os
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

import mini_claude.session as session_module
from mini_claude.artifact_archive import ArtifactArchive
from mini_claude.projections import (
    ModelReplayProjection,
    RunTraceProjection,
    SessionProjection,
)
from mini_claude.recovery import RecoveryProjection
from mini_claude.runtime_event import RuntimeEvent
from mini_claude.runtime_store import SQLiteRuntimeStore

from runtime_fixtures import FaultInjector, build_scenario, scenario_events


SRC_ROOT = Path(__file__).parents[2]


def _events(provider: str = "fixture") -> list[RuntimeEvent]:
    return [
        RuntimeEvent.from_dict(item)
        for item in scenario_events(build_scenario(), provider=provider)
    ]


def test_projections_are_provider_neutral_and_rebuildable(tmp_path: Path):
    anthropic = _events("anthropic")
    openai = _events("openai")
    assert SessionProjection().build(anthropic).messages == SessionProjection().build(openai).messages
    assert ModelReplayProjection().build(anthropic).messages == ModelReplayProjection().build(openai).messages
    assert RunTraceProjection().build(anthropic).to_dict()["digest"] == RunTraceProjection().build(openai).to_dict()["digest"]

    archive = ArtifactArchive(tmp_path / "artifacts")
    reference = archive.archive("same body", mime_type="text/plain", encoding="utf-8")
    assert archive.inspect(reference).sha256 == reference.sha256

    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        for event in anthropic:
            store.append(event)
        recovery = RecoveryProjection().scan(store)
        assert recovery and recovery[0].source_digest


def test_fault_matrix_is_fail_closed_without_canonical_rewrite(tmp_path: Path):
    event = _events()[0]
    with pytest.raises(RuntimeError, match="fixture fault at store.commit"):
        with SQLiteRuntimeStore(
            tmp_path / "fault.sqlite", fault_hook=FaultInjector("store.commit")
        ) as store:
            store.append(event)

    archive = ArtifactArchive(
        tmp_path / "artifacts", fault_hook=FaultInjector("artifact.fsync")
    )
    with pytest.raises(RuntimeError, match="fixture fault at artifact.fsync"):
        archive.archive("x" * 100)
    assert not list((tmp_path / "artifacts").rglob("*.json"))


def test_cli_resume_is_offline_and_uses_only_session_scoped_canonical_store(
    tmp_path: Path,
):
    home = tmp_path / "home"
    session_root = home / ".mini-claude" / "sessions"
    session_root.mkdir(parents=True)
    (home / ".mini-claude" / "runtime.sqlite").write_bytes(b"old root")
    (session_root / "legacy.json").write_text("{\"metadata\":{}}", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "PYTHONPATH": str(SRC_ROOT),
            "PYTHON_DOTENV_DISABLED": "1",
        }
    )
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-m", "mini_claude", "--resume"],
        cwd=SRC_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "No canonical sessions found." in result.stdout


def test_session_listing_never_reads_flat_or_root_legacy_files(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path / "sessions")
    session_module.SESSION_DIR.mkdir(parents=True)
    flat = session_module.SESSION_DIR / "legacy.json"
    root = tmp_path / "runtime.sqlite"
    flat.write_text("{}", encoding="utf-8")
    root.write_bytes(b"old root")
    before = (hashlib.sha256(flat.read_bytes()).digest(), hashlib.sha256(root.read_bytes()).digest())
    assert session_module.list_runtime_store_paths() == []
    assert session_module.list_sessions() == []
    assert before == (
        hashlib.sha256(flat.read_bytes()).digest(),
        hashlib.sha256(root.read_bytes()).digest(),
    )


def test_runtime_package_has_no_removed_logging_route():
    source_root = Path(__file__).parents[1]
    removed_symbols = (
        "AgentLogger",
        "SessionTracer",
        "LegacyShadowSink",
        "CompositeEventSink",
        "LegacyToolResultsAdapter",
        "AuthorityGate",
    )
    for path in source_root.glob("*.py"):
        if path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8")
        assert not any(symbol in text for symbol in removed_symbols), path
    assert not (source_root / "logger.py").exists()
    assert not (source_root / "tracer.py").exists()
    assert not (source_root / "cutover.py").exists()
    assert not (source_root / "shadow_parity.py").exists()
