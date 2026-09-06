"""Harbor adapter for running Mini Claude Code in Terminal-Bench containers."""

from __future__ import annotations

import asyncio
import json
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
REMOTE_ROOT = "/tmp/mini-claude-py"
REMOTE_VENV = f"{REMOTE_ROOT}/.venv"
REMOTE_PYTHON = f"{REMOTE_VENV}/bin/python"
REMOTE_RUNTIME_DIR = "/logs/agent/runtime"
PREBUILT_RUNTIME_IMPORT_CHECK = (
    "import anthropic, openai, dotenv, rich, mini_claude"
)
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_TIMEOUT_SEC = 1800
PRICE_PER_MILLION_TOKENS = {
    "cache_read_input": 0.007,
    "uncached_input": 0.22,
    "output": 0.66,
}

# Canonical runtime data is redirected into Harbor's bind-mounted agent log
# directory by _runtime_env().  This keeps already-committed events available
# even when Harbor cancels the outer environment.exec call on timeout.
REMOTE_METRICS_SCRIPT = r"""
import json
import os
from pathlib import Path

runtime_dir = os.environ.get("MINI_CLAUDE_RUNTIME_DIR")
root = (Path(runtime_dir).expanduser() if runtime_dir else Path.home() / ".mini-claude") / "sessions"

# The SQLite ledger is the durable source of truth.  A timed-out agent may
# still have many committed usage events but no final session.v2.json yet.
databases = [path for path in root.glob("*/runtime.sqlite") if path.is_file()]
if databases:
    try:
        from mini_claude.projections.metrics_projection import CanonicalMetricsProjection
        from mini_claude.runtime_store import SQLiteRuntimeStore

        database = max(databases, key=lambda path: path.stat().st_mtime_ns)
        store = SQLiteRuntimeStore(database, timeout=10)
        try:
            metrics = CanonicalMetricsProjection().build(store).to_dict()
        finally:
            store.close()
        print(json.dumps(metrics, separators=(",", ":")))
        raise SystemExit
    except Exception:
        # Fall back to the disposable snapshot if the ledger is temporarily
        # locked or the runtime package is unavailable.
        pass

snapshots = [path for path in root.glob("*/session.v2.json") if path.is_file()]
if snapshots:
    latest = max(snapshots, key=lambda path: path.stat().st_mtime_ns)
    snapshot = json.loads(latest.read_text(encoding="utf-8"))
    print(json.dumps(snapshot.get("metrics") or {}, separators=(",", ":")))
else:
    print("{}")
"""


def _summarize_canonical_metrics(metrics: Mapping[str, Any]) -> dict[str, int] | None:
    """Convert canonical per-run metrics into Harbor's usage shape.

    Harbor defines ``n_input_tokens`` as total input, including cached input.
    OpenAI-compatible usage already follows that convention, while Anthropic
    reports uncached input, cache writes, and cache reads as separate fields.
    """

    runs = metrics.get("runs")
    if not isinstance(runs, list):
        return None

    usage_runs = [
        run
        for run in runs
        if isinstance(run, Mapping) and run.get("usage_available") is True
    ]
    if not usage_runs:
        return None

    def value(run: Mapping[str, Any], key: str) -> int:
        try:
            return max(int(run.get(key, 0) or 0), 0)
        except (TypeError, ValueError):
            return 0

    cache_read_tokens = sum(value(run, "cache_read_tokens") for run in usage_runs)
    cache_create_tokens = sum(value(run, "cache_create_tokens") for run in usage_runs)
    output_tokens = sum(value(run, "output_tokens") for run in usage_runs)

    input_tokens = 0
    for run in usage_runs:
        reported_input = value(run, "input_tokens")
        provider = str(run.get("provider") or "").lower()
        if provider == "anthropic":
            # Anthropic exposes these as separate usage components.  Cache
            # writes are still uncached input for the requested price model.
            input_tokens += reported_input + value(run, "cache_read_tokens") + value(
                run, "cache_create_tokens"
            )
        else:
            # OpenAI prompt_tokens, and canonical data from other providers,
            # already represent total input including any cached subset.
            input_tokens += reported_input

    return {
        "input_tokens": input_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_create_tokens": cache_create_tokens,
        "output_tokens": output_tokens,
    }


def _calculate_cost_usd(usage: Mapping[str, Any]) -> float:
    """Calculate cost when input usage includes cached input tokens."""

    input_tokens = max(int(usage.get("input_tokens", 0) or 0), 0)
    cache_read_tokens = max(int(usage.get("cache_read_tokens", 0) or 0), 0)
    output_tokens = max(int(usage.get("output_tokens", 0) or 0), 0)

    # Harbor's n_input_tokens includes cached tokens.  A provider should never
    # report more cached tokens than total input, but clamp malformed data so a
    # bad response cannot produce negative uncached input or a negative price.
    cache_read_tokens = min(cache_read_tokens, input_tokens)
    uncached_input_tokens = input_tokens - cache_read_tokens
    return round(
        (
            uncached_input_tokens * PRICE_PER_MILLION_TOKENS["uncached_input"]
            + cache_read_tokens * PRICE_PER_MILLION_TOKENS["cache_read_input"]
            + output_tokens * PRICE_PER_MILLION_TOKENS["output"]
        )
        / 1_000_000,
        8,
    )


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

    def _environment_log_path(self, filename: str) -> str:
        return shlex.quote(str(self.environment_logs_dir / filename))

    def _logged_command(self, command: str, *, stdout_name: str, stderr_name: str) -> str:
        """Run a command while appending output to Harbor's mounted log dir."""

        log_dir = shlex.quote(str(self.environment_logs_dir))
        stdout_path = self._environment_log_path(stdout_name)
        stderr_path = self._environment_log_path(stderr_name)
        return (
            f"mkdir -p {log_dir} && "
            f"({command}) >> {stdout_path} 2>> {stderr_path}"
        )

    def _local_log_text(self, filename: str) -> str:
        """Read a live-mounted log for useful errors after exec returns."""

        path = self.logs_dir / filename
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ""

    def _local_canonical_metrics(self) -> Mapping[str, Any] | None:
        """Rebuild metrics from Harbor's host-side runtime mount.

        The agent log directory is bind-mounted for Docker environments.  This
        fallback remains available when an outer Harbor timeout has already
        made a final command inside the container unavailable.
        """

        runtime_root = self.logs_dir / "runtime" / "sessions"
        databases = [
            path for path in runtime_root.glob("*/runtime.sqlite") if path.is_file()
        ]
        if not databases:
            return None

        try:
            from mini_claude.projections.metrics_projection import CanonicalMetricsProjection
            from mini_claude.runtime_store import SQLiteRuntimeStore

            database = max(databases, key=lambda path: path.stat().st_mtime_ns)
            store = SQLiteRuntimeStore(database, timeout=10)
            try:
                metrics = CanonicalMetricsProjection().build(store).to_dict()
            finally:
                store.close()
            return metrics
        except Exception:
            return None

    @staticmethod
    def _usage_from_metrics(
        metrics: Mapping[str, Any] | None,
    ) -> tuple[dict[str, int] | None, str | None]:
        if metrics is None:
            return None, "canonical metrics unavailable"
        if not isinstance(metrics, Mapping):
            return None, "canonical metrics payload is not an object"
        usage = _summarize_canonical_metrics(metrics)
        if usage is None:
            return None, "no completed usage records in canonical metrics"
        return usage, None

    async def _collect_usage(
        self,
        environment: BaseEnvironment,
        workdir: str,
    ) -> tuple[dict[str, int] | None, str | None]:
        """Collect usage from the remote ledger, then the local log mount."""

        errors: list[str] = []
        try:
            metrics_result = await environment.exec(
                f"{shlex.quote(REMOTE_PYTHON)} -c {shlex.quote(REMOTE_METRICS_SCRIPT)}",
                cwd=workdir,
                env=self._runtime_env(),
                timeout_sec=30,
            )
            if metrics_result.return_code != 0:
                errors.append(
                    metrics_result.stderr or metrics_result.stdout or "remote metrics read failed"
                )
            else:
                metrics = json.loads((metrics_result.stdout or "{}").strip() or "{}")
                usage, usage_error = self._usage_from_metrics(metrics)
                if usage is not None:
                    return usage, None
                if usage_error:
                    errors.append(usage_error)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            errors.append(f"invalid canonical metrics: {error}")
        except Exception as error:
            errors.append(f"remote canonical metrics unavailable: {error}")

        usage, usage_error = self._usage_from_metrics(self._local_canonical_metrics())
        if usage is not None:
            return usage, None
        if usage_error:
            errors.append(usage_error)
        return None, "; ".join(errors)[:500] or "canonical metrics unavailable"

    @staticmethod
    def _record_usage(
        context: AgentContext,
        *,
        workdir: str,
        model: str,
        return_code: int | None,
        usage: dict[str, int] | None,
        usage_error: str | None,
    ) -> None:
        """Write usage to the Harbor context before any agent error is raised."""

        context.metadata = {
            "workdir": workdir,
            "model": model,
            "return_code": return_code,
            "usage_source": "canonical-session-v2-or-sqlite",
            "usage_available": usage is not None,
        }
        if usage is not None:
            context.n_input_tokens = usage["input_tokens"]
            context.n_cache_tokens = usage["cache_read_tokens"]
            context.n_output_tokens = usage["output_tokens"]
            context.cost_usd = _calculate_cost_usd(usage)
            context.metadata["cache_create_tokens"] = usage["cache_create_tokens"]
            context.metadata["usage_pricing"] = {
                "currency": "USD",
                "per_million_tokens": dict(PRICE_PER_MILLION_TOKENS),
                "uncached_input_tokens": max(
                    usage["input_tokens"] - usage["cache_read_tokens"], 0
                ),
                "cost_usd": context.cost_usd,
            }
        elif usage_error:
            context.metadata["usage_error"] = usage_error

    async def _prebuilt_runtime_ready(self, environment: BaseEnvironment) -> bool:
        """Check whether the task image already contains the agent runtime.

        The source tree is uploaded before this check, so an image can carry a
        pre-created venv and editable install at ``REMOTE_ROOT`` while still
        receiving the current working-tree source.  A failed check is treated
        as a normal cache miss and falls back to the existing setup path.
        """

        result = await environment.exec(
            f"test -x {shlex.quote(REMOTE_PYTHON)} && "
            f"{shlex.quote(REMOTE_PYTHON)} -c "
            f"{shlex.quote(PREBUILT_RUNTIME_IMPORT_CHECK)}",
            timeout_sec=10,
        )
        return result.return_code == 0

    async def setup(self, environment: BaseEnvironment) -> None:
        """Upload and prepare Mini Claude in the isolated task container."""
        await environment.upload_dir(SOURCE_ROOT, REMOTE_ROOT)

        if await self._prebuilt_runtime_ready(environment):
            return

        bootstrap = (
            "if ! (command -v python3 >/dev/null 2>&1 && "
            "python3 -m venv --help >/dev/null 2>&1); then "
            "apt-get update && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y "
            "--no-install-recommends python3 python3-venv; "
            "fi && "
            f"python3 -m venv {shlex.quote(REMOTE_VENV)}"
        )
        result = await environment.exec(
            self._logged_command(
                bootstrap,
                stdout_name="setup.stdout.txt",
                stderr_name="setup.stderr.txt",
            ),
            timeout_sec=600,
        )
        if result.return_code != 0:
            output = (
                result.stderr
                or result.stdout
                or self._local_log_text("setup.stderr.txt")
                or self._local_log_text("setup.stdout.txt")
                or "no output"
            )
            raise RuntimeError(f"Python bootstrap failed: {output}")

        result = await environment.exec(
            self._logged_command(
                (
                    f"{shlex.quote(REMOTE_PYTHON)} -m pip install "
                    f"--disable-pip-version-check --no-cache-dir -e "
                    f"{shlex.quote(REMOTE_ROOT)}"
                ),
                stdout_name="setup.stdout.txt",
                stderr_name="setup.stderr.txt",
            ),
            timeout_sec=600,
        )
        if result.return_code != 0:
            output = (
                result.stderr
                or result.stdout
                or self._local_log_text("setup.stderr.txt")
                or self._local_log_text("setup.stdout.txt")
                or "no output"
            )
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
        env = {
            key: value
            for key in keys
            if (value := self._get_setting(key))
        }
        # This directory is bind-mounted by Harbor as /logs/agent.  Keep it
        # separate from the task's HOME so task commands retain their normal
        # user configuration while canonical runtime data remains durable.
        env["MINI_CLAUDE_RUNTIME_DIR"] = REMOTE_RUNTIME_DIR
        return env

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

        agent_command = (
            f"{shlex.quote(REMOTE_PYTHON)} -u -m mini_claude --yolo "
            f"--model {shlex.quote(self._get_cli_model())} "
            f"{shlex.quote(instruction)} < /dev/null"
        )
        result = None
        primary_error: BaseException | None = None
        try:
            result = await environment.exec(
                self._logged_command(
                    agent_command,
                    stdout_name="stdout.txt",
                    stderr_name="stderr.txt",
                ),
                cwd=workdir,
                env=self._runtime_env(),
                timeout_sec=DEFAULT_TIMEOUT_SEC,
            )
        except BaseException as error:
            # Harbor wraps this method in wait_for().  Preserve the original
            # cancellation/exception, but first harvest already committed
            # usage from the durable runtime ledger.
            primary_error = error

        usage: dict[str, int] | None = None
        usage_error: str | None = None
        try:
            # shield() lets this best-effort cleanup complete after the outer
            # wait_for has delivered its cancellation.
            usage, usage_error = await asyncio.shield(
                self._collect_usage(environment, workdir)
            )
        except asyncio.CancelledError as error:
            usage_error = f"usage collection cancelled: {error}"
            if primary_error is None:
                primary_error = error
        except Exception as error:
            usage_error = f"canonical metrics unavailable: {error}"

        self._record_usage(
            context,
            workdir=workdir,
            model=self._get_cli_model(),
            return_code=getattr(result, "return_code", None),
            usage=usage,
            usage_error=usage_error,
        )

        if primary_error is not None:
            raise primary_error

        assert result is not None

        if result.return_code != 0:
            output = (
                result.stderr
                or result.stdout
                or self._local_log_text("stderr.txt")
                or self._local_log_text("stdout.txt")
                or "no output"
            )
            raise RuntimeError(
                f"Mini Claude exited with code {result.return_code}: {output}"
            )
