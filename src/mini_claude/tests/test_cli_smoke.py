"""Offline CLI smoke entry points reserved for the canonical resume changes."""

from __future__ import annotations

import os
import subprocess
import sys
import hashlib
from pathlib import Path

import pytest

import mini_claude.logger as logger_module
import mini_claude.session as session_module
from mini_claude import __main__ as cli_module
from mini_claude.agent import Agent
from mini_claude.runtime_event import RuntimeEvent

SRC_ROOT = Path(__file__).parents[2]


def _isolated_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env["PYTHONPATH"] = str(SRC_ROOT)
    return env


def test_cli_help_smoke_is_offline(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, "-m", "mini_claude", "--help"],
        cwd=SRC_ROOT,
        env=_isolated_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "--resume" in result.stdout
    assert "Usage:" in result.stdout


def test_resume_smoke_uses_isolated_home(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, "-m", "mini_claude", "--resume"],
        cwd=SRC_ROOT,
        env=_isolated_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_cli_fake_provider_covers_one_shot_list_latest_resume_shadow_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], provider: str
):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    session_root = home / ".mini-claude" / "sessions"
    monkeypatch.setattr(session_module, "SESSION_DIR", session_root)
    monkeypatch.setattr(logger_module, "SESSION_DIR", session_root)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key" if provider == "anthropic" else "")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key" if provider == "openai" else "")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")

    async def fake_anthropic(self: Agent, user_message: str) -> None:
        self._runtime_emitter.emit(RuntimeEvent.create(
            self._runtime_context,
            role="model",
            author="agent",
            content={"kind": "text", "text": f"fake reply: {user_message}"},
            metadata={"lifecycle": "model_final", "provider": "anthropic"},
        ))
        self._emit_text(f"fake reply: {user_message}")

    async def fake_openai(self: Agent, user_message: str) -> None:
        self._runtime_emitter.emit(RuntimeEvent.create(
            self._runtime_context,
            role="model",
            author="agent",
            content={"kind": "text", "text": f"fake reply: {user_message}"},
            metadata={"lifecycle": "model_final", "provider": "openai"},
        ))
        self._emit_text(f"fake reply: {user_message}")

    monkeypatch.setattr(Agent, "_chat_anthropic", fake_anthropic)
    monkeypatch.setattr(Agent, "_chat_openai", fake_openai)

    args = ["--log-authority", "shadow"]
    if provider == "openai":
        args += ["--api-base", "https://fake-provider.invalid/v1"]
    monkeypatch.setattr(sys, "argv", ["mini-claude", *args, "one-shot"])
    cli_module.main()
    output = capsys.readouterr().out
    assert "fake reply: one-shot" in output

    databases = sorted(session_root.glob("*/runtime.sqlite"))
    assert len(databases) == 1
    session_id = databases[0].parent.name
    legacy_log = session_root / session_id / "logs" / "001.jsonl"
    assert legacy_log.exists()
    assert "one-shot" in legacy_log.read_text(encoding="utf-8")

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["mini-claude", "--list"])
    cli_module.main()
    assert session_id in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["mini-claude", "--latest"])
    cli_module.main()
    assert f"Latest session: {session_id}" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["mini-claude", "--resume"])
    cli_module.main()
    assert "Canonical session available" in capsys.readouterr().out

    if provider == "anthropic":
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    else:
        monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    rollback_args = ["--log-rollback"]
    if provider == "openai":
        rollback_args += ["--api-base", "https://fake-provider.invalid/v1"]
    monkeypatch.setattr(sys, "argv", ["mini-claude", *rollback_args, "rollback"])
    cli_module.main()
    capsys.readouterr()
    assert databases[0].exists()


def test_cli_canonical_cutover_and_post_cutover_smoke_preserves_legacy_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """Execute the approved G9 route in an isolated HOME and verify rollback."""

    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    session_root = home / ".mini-claude" / "sessions"
    monkeypatch.setattr(session_module, "SESSION_DIR", session_root)
    monkeypatch.setattr(logger_module, "SESSION_DIR", session_root)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")

    async def fake_anthropic(self: Agent, user_message: str) -> None:
        self._runtime_emitter.emit(RuntimeEvent.create(
            self._runtime_context,
            role="model",
            author="agent",
            content={"kind": "text", "text": f"canonical reply: {user_message}"},
            metadata={"lifecycle": "model_final", "provider": "anthropic"},
        ))
        self._emit_text(f"canonical reply: {user_message}")

    monkeypatch.setattr(Agent, "_chat_anthropic", fake_anthropic)

    monkeypatch.setattr(sys, "argv", [
        "mini-claude", "--log-authority", "shadow", "before-cutover"
    ])
    cli_module.main()
    assert "canonical reply: before-cutover" in capsys.readouterr().out

    database = next(session_root.glob("*/runtime.sqlite"))
    session_id = database.parent.name
    legacy_log = session_root / session_id / "logs" / "001.jsonl"
    legacy_before = hashlib.sha256(legacy_log.read_bytes()).hexdigest()
    canonical_before = database.read_bytes()

    monkeypatch.setattr(sys, "argv", [
        "mini-claude", "--resume", "--log-authority", "canonical",
        "--approve-canonical", "after-cutover",
    ])
    cli_module.main()
    assert "canonical reply: after-cutover" in capsys.readouterr().out
    assert database.read_bytes() != canonical_before
    assert hashlib.sha256(legacy_log.read_bytes()).hexdigest() == legacy_before

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["mini-claude", "--list"])
    cli_module.main()
    assert session_id in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["mini-claude", "--latest"])
    cli_module.main()
    assert f"Latest session: {session_id}" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["mini-claude", "--resume"])
    cli_module.main()
    assert "Canonical session available" in capsys.readouterr().out

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    monkeypatch.setattr(sys, "argv", [
        "mini-claude", "--resume", "--log-rollback", "after-rollback"
    ])
    cli_module.main()
    assert "canonical reply: after-rollback" in capsys.readouterr().out
    assert database.exists()
    assert any(
        "after-rollback" in path.read_text(encoding="utf-8")
        for path in (session_root / session_id / "logs").glob("*.jsonl")
    )
