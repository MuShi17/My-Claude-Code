"""Harbor adapter for running Mini Claude Code in Terminal-Bench containers."""

from __future__ import annotations

import shlex
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
REMOTE_ROOT = "/tmp/mini-claude-py"
REMOTE_VENV = f"{REMOTE_ROOT}/.venv"
REMOTE_PYTHON = f"{REMOTE_VENV}/bin/python"
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_TIMEOUT_SEC = 1800


def _load_project_env() -> dict[str, str]:
    """Read the local .env template without exporting it to the host process."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


PROJECT_ENV = _load_project_env()


class MiniClaudeHarborAgent(BaseAgent):
    """Run the repository's CLI inside the Harbor task environment."""

    @staticmethod
    def name() -> str:
        return "mini-claude-py"

    def version(self) -> str | None:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        """Upload and install Mini Claude in the isolated task container."""
        await environment.upload_dir(SOURCE_ROOT, REMOTE_ROOT)

        bootstrap = (
            "if ! (command -v python3 >/dev/null 2>&1 && "
            "python3 -m venv --help >/dev/null 2>&1); then "
            "apt-get update && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y "
            "--no-install-recommends python3 python3-venv; "
            "fi && "
            f"python3 -m venv {shlex.quote(REMOTE_VENV)}"
        )
        result = await environment.exec(bootstrap, timeout_sec=600)
        if result.return_code != 0:
            output = result.stderr or result.stdout or "no output"
            raise RuntimeError(f"Python bootstrap failed: {output}")

        result = await environment.exec(
            f"{shlex.quote(REMOTE_PYTHON)} -m pip install "
            f"--disable-pip-version-check --no-cache-dir -e "
            f"{shlex.quote(REMOTE_ROOT)}",
            timeout_sec=600,
        )
        if result.return_code != 0:
            output = result.stderr or result.stdout or "no output"
            raise RuntimeError(f"Mini Claude setup failed: {output}")

    def _get_setting(self, key: str) -> str | None:
        """Resolve Harbor/host environment values before the project .env file."""
        return self._get_env(key) or PROJECT_ENV.get(key)

    def _get_cli_model(self) -> str:
        """Resolve the model id expected by the configured API endpoint."""
        explicit = self._get_setting("MINI_CLAUDE_MODEL_ID")
        if explicit:
            return explicit

        model = self.model_name or self._get_setting("MINI_CLAUDE_MODEL") or DEFAULT_MODEL
        # Harbor model names are normally provider/model. Anthropic and direct
        # OpenAI endpoints expect only the provider-local model id.
        if model.startswith("anthropic/"):
            return model.split("/", 1)[1]
        if model.startswith("openai/") and "openrouter.ai" not in (
            self._get_setting("OPENAI_BASE_URL") or ""
        ):
            return model.split("/", 1)[1]
        return model

    def _runtime_env(self) -> dict[str, str]:
        """Forward only model/API settings; never forward the whole host env."""
        model = self.model_name or self._get_setting("MINI_CLAUDE_MODEL") or ""
        provider = model.split("/", 1)[0].lower() if "/" in model else ""
        if provider == "anthropic":
            keys = (
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_BASE_URL",
            )
        elif provider == "openai":
            keys = (
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
            )
        else:
            keys = (
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_BASE_URL",
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
            )
        keys += (
            "MINI_CLAUDE_MODEL",
            "MINI_CLAUDE_MODEL_ID",
            "MINI_CLAUDE_THINKING_EFFORT",
        )
        return {
            key: value
            for key in keys
            if (value := self._get_setting(key))
        }

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """Execute one Mini Claude one-shot session in the task workspace."""
        workdir = getattr(environment.task_env_config, "workdir", None)
        if not workdir:
            pwd_result = await environment.exec("pwd", timeout_sec=10)
            if pwd_result.return_code != 0 or not pwd_result.stdout:
                raise RuntimeError("Unable to determine the task work directory")
            workdir = pwd_result.stdout.strip().splitlines()[-1]

        command = (
            f"{shlex.quote(REMOTE_PYTHON)} -m mini_claude --yolo "
            f"--model {shlex.quote(self._get_cli_model())} "
            f"{shlex.quote(instruction)} < /dev/null"
        )
        result = await environment.exec(
            command,
            cwd=workdir,
            env=self._runtime_env(),
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "stdout.txt").write_text(
            result.stdout or "", encoding="utf-8"
        )
        (self.logs_dir / "stderr.txt").write_text(
            result.stderr or "", encoding="utf-8"
        )
        context.metadata = {
            "workdir": workdir,
            "return_code": result.return_code,
            "model": self._get_cli_model(),
        }

        if result.return_code != 0:
            output = result.stderr or result.stdout or "no output"
            raise RuntimeError(
                f"Mini Claude exited with code {result.return_code}: {output}"
            )
