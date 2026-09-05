"""C11 parity, failure-gate, authority and offline rollback evidence."""

from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

from mini_claude.artifact_archive import ArtifactArchive
from mini_claude.cutover import (
    AuthorityConfig,
    AuthorityGate,
    CutoverBlockedError,
    GapRegister,
    ParityMismatch,
    StableSemanticComparator,
    render_acceptance_report,
    select_event_sink,
)
from mini_claude.event_sink import LegacyShadowSink, RecordingEventSink
from mini_claude.recovery import RecoveryProjection
from mini_claude.runtime_event import RuntimeEvent
from mini_claude.runtime_store import SQLiteRuntimeStore
from mini_claude.projections import ModelReplayProjection, RunTraceProjection, SessionProjection

from runtime_fixtures import FaultInjector, build_scenario, scenario_events, stable_projection


SRC_ROOT = Path(__file__).parents[2]


def _events(provider: str) -> list[dict]:
    return scenario_events(build_scenario(), provider=provider)


def test_dual_provider_semantic_parity_and_allowed_metadata_report():
    comparator = StableSemanticComparator()
    report = comparator.compare(
        _events("anthropic"),
        _events("openai"),
        scenario="provider-parity",
        evidence=("C02 FakeProviderScript", "C06 ModelCallRecorder"),
    )
    assert report.equal is True
    assert not report.blockers
    assert report.allowed_differences
    assert any("provider" in item.path for item in report.allowed_differences)


def test_missing_event_pairing_or_ref_is_a_blocker_and_gap_is_traceable():
    comparator = StableSemanticComparator()
    left = _events("anthropic")
    right = _events("openai")[:-1]
    report = comparator.compare(left, right, scenario="missing-function-response", evidence=("C11-1.2",))
    assert report.equal is False
    assert report.blockers
    gap = GapRegister()
    gap.add_report(report)
    assert gap.blockers
    markdown = gap.to_markdown()
    assert "missing-function-response" in markdown
    assert "C11-1.2" in markdown


def test_session_model_trace_artifact_and_recovery_evidence_is_stable(tmp_path: Path):
    left = [RuntimeEvent.from_dict(item) for item in _events("anthropic")]
    right = [RuntimeEvent.from_dict(item) for item in _events("openai")]
    comparator = StableSemanticComparator()
    reports = [
        comparator.compare(SessionProjection().build(left), SessionProjection().build(right), scenario="session"),
        comparator.compare(ModelReplayProjection().build(left), ModelReplayProjection().build(right), scenario="model"),
        comparator.compare(RunTraceProjection().build(left), RunTraceProjection().build(right), scenario="trace"),
    ]
    assert all(report.equal for report in reports)
    archive = ArtifactArchive(tmp_path / "artifacts")
    ref = archive.archive("same body", mime_type="text/plain", encoding="utf-8")
    artifact_report = comparator.compare(ref.to_dict(), ref.to_dict(), scenario="artifact")
    assert artifact_report.equal

    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        for event in left:
            store.append(event)
        recovery = RecoveryProjection().scan(store)
        recovery_report = comparator.compare(recovery[0].to_dict(), recovery[0].to_dict(), scenario="recovery")
        assert recovery_report.equal


def test_authority_gate_blocks_canonical_with_unclosed_blocker_and_rolls_back_safely():
    canonical = RecordingEventSink()
    legacy = RecordingEventSink()
    gap = GapRegister([ParityMismatch("scenario", "blocker", "events[2]", "missing", owner="owner", evidence=("test",))])
    with pytest.raises(CutoverBlockedError):
        select_event_sink(canonical, legacy, AuthorityConfig(mode="canonical"), gaps=gap, approved=True)
    assert select_event_sink(canonical, legacy, AuthorityConfig(mode="shadow"), approved=False) is not canonical
    assert select_event_sink(canonical, legacy, AuthorityConfig(mode="legacy", rollback=True)) is legacy
    gap.close(scenario="scenario", path="events[2]")
    assert AuthorityGate().require_approved(AuthorityConfig(mode="canonical"), approved=True, gaps=gap) == "canonical"


def test_legacy_sink_failure_is_diagnostic_but_canonical_remains_authoritative():
    class FailingLegacy:
        def log_runtime_event(self, event):
            raise OSError("legacy unavailable")

    canonical = RecordingEventSink()
    legacy = LegacyShadowSink(FailingLegacy())
    sink = select_event_sink(canonical, legacy, AuthorityConfig(mode="shadow"))
    event = RuntimeEvent.from_dict(_events("anthropic")[0])
    sink.emit(event)
    assert [item.id for item in canonical.events] == [event.id]
    assert canonical.events[0].kind == event.kind
    assert legacy.failures


def test_representative_fault_matrix_is_explicit_and_fail_closed(tmp_path: Path):
    with pytest.raises(RuntimeError, match="fixture fault at store.commit"):
        with SQLiteRuntimeStore(tmp_path / "fault.sqlite", fault_hook=FaultInjector("store.commit")) as store:
            store.append(RuntimeEvent.from_dict(_events("anthropic")[0]))
    archive = ArtifactArchive(tmp_path / "artifacts", fault_hook=FaultInjector("artifact.fsync"))
    with pytest.raises(RuntimeError, match="fixture fault at artifact.fsync"):
        archive.archive("x" * 100)
    assert not list((tmp_path / "artifacts").rglob("*.json"))


def test_cli_resume_in_isolated_home_is_offline_and_does_not_touch_user_data(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONPATH"] = str(SRC_ROOT)
    env["PYTHON_DOTENV_DISABLED"] = "1"
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
    assert "No previous sessions" in result.stdout
    assert not (home / ".mini-claude" / "sessions").exists()


def test_cli_resume_reports_canonical_then_legacy_readonly_without_network(tmp_path: Path):
    events = [RuntimeEvent.from_dict(item) for item in _events("anthropic")]
    home = tmp_path / "home"
    database = home / ".mini-claude" / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        store.append(events[0])
    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PYTHONPATH": str(SRC_ROOT),
        "PYTHON_DOTENV_DISABLED": "1",
    })
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    canonical = subprocess.run(
        [sys.executable, "-m", "mini_claude", "--resume"],
        cwd=SRC_ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    assert canonical.returncode == 0
    assert "Canonical session available" in canonical.stdout

    canonical_bytes = database.read_bytes()
    database.unlink()
    legacy_path = home / ".mini-claude" / "sessions" / "legacy-only" / "session.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(json.dumps({
        "metadata": {"id": "legacy-only"},
        "anthropicMessages": [{"role": "user", "content": "legacy"}],
    }), encoding="utf-8")
    legacy = subprocess.run(
        [sys.executable, "-m", "mini_claude", "--resume"],
        cwd=SRC_ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    assert legacy.returncode == 0
    assert "Legacy readonly session available" in legacy.stdout
    assert legacy_path.read_text(encoding="utf-8").find("legacy") >= 0
    assert canonical_bytes


def test_acceptance_report_records_commands_gaps_and_no_cutover_side_effect():
    report = StableSemanticComparator().compare({"events": [1]}, {"events": [1]}, scenario="clean")
    rendered = render_acceptance_report(
        [report],
        commands=("py313 -m pytest -q src/mini_claude/tests", "openspec validate --changes --strict --no-interactive"),
        test_results=("full suite exit_code=0; pass count is collected from the test runner",),
    )
    assert "Canonical Runtime Event Acceptance Report" in rendered
    assert "full suite exit_code=0" in rendered
    assert "109 passed" not in rendered
    assert "explicit approval" in rendered
