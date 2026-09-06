"""Offline CLI smoke entry points reserved for the canonical resume changes."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

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
def test_cli_fake_provider_covers_canonical_one_shot_list_latest_and_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], provider: str
):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    session_root = home / ".mini-claude" / "sessions"
    monkeypatch.setattr(session_module, "SESSION_DIR", session_root)
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

    args: list[str] = []
    if provider == "openai":
        args += ["--api-base", "https://fake-provider.invalid/v1"]
    monkeypatch.setattr(sys, "argv", ["mini-claude", *args, "one-shot"])
    cli_module.main()
    output = capsys.readouterr().out
    assert "fake reply: one-shot" in output

    databases = sorted(session_root.glob("*/runtime.sqlite"))
    assert len(databases) == 1
    session_id = databases[0].parent.name
    assert not (session_root / session_id / "logs").exists()
    assert (session_root / session_id / "session.v2.json").exists()

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["mini-claude", "--list"])
    cli_module.main()
    assert session_id in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["mini-claude", "--latest"])
    cli_module.main()
    assert f"Latest session: {session_id} (canonical)" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["mini-claude", "--resume"])
    cli_module.main()
    assert "Canonical session available" in capsys.readouterr().out

    if provider == "anthropic":
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    else:
        monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    resume_args = ["--resume"]
    if provider == "openai":
        resume_args += ["--api-base", "https://fake-provider.invalid/v1"]
    monkeypatch.setattr(sys, "argv", ["mini-claude", *resume_args, "resume-turn"])
    cli_module.main()
    assert "fake reply: resume-turn" in capsys.readouterr().out
    assert databases[0].exists()


def test_cli_legacy_files_are_ignored_and_old_authority_flags_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    session_root = home / ".mini-claude" / "sessions"
    monkeypatch.setattr(session_module, "SESSION_DIR", session_root)
    legacy_dir = session_root / "legacy-session"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "session.json").write_text(
        '{"metadata":{"id":"legacy-session"}}', encoding="utf-8"
    )
    (session_root / "flat-session.json").write_text("{}", encoding="utf-8")
    (home / ".mini-claude" ).mkdir(parents=True, exist_ok=True)
    (home / ".mini-claude" / "runtime.sqlite").write_bytes(b"old root")

    monkeypatch.setattr(sys, "argv", ["mini-claude", "--list"])
    cli_module.main()
    assert "No previous sessions found." in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["mini-claude", "--latest"])
    cli_module.main()
    assert "No previous sessions found." in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["mini-claude", "--log-authority", "shadow"])
    with pytest.raises(SystemExit) as error:
        cli_module.main()
    assert error.value.code == 2
