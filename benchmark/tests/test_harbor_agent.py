from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from harbor.models.agent.context import AgentContext

from benchmark.harbor_agent import RolloHarborAgent


class _Result:
    def __init__(self, return_code: int, stdout: str = "", stderr: str = "") -> None:
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr


class _Environment:
    task_env_config = SimpleNamespace(workdir="/app")

    def __init__(self, *, cancelled: bool, delay_first: bool = False) -> None:
        self.cancelled = cancelled
        self.delay_first = delay_first
        self.calls: list[str] = []

    async def exec(self, command: str, **kwargs) -> _Result:
        del kwargs
        self.calls.append(command)
        if self.delay_first and len(self.calls) == 1:
            await asyncio.sleep(10)
        if self.cancelled and len(self.calls) == 1:
            raise asyncio.CancelledError()
        if "-c" in command:
            return _Result(
                0,
                stdout=(
                    '{"runs":[{"provider":"anthropic",'
                    '"usage_available":true,"input_tokens":10,'
                    '"cache_read_tokens":20,"cache_create_tokens":0,'
                    '"output_tokens":3}]}'
                ),
            )
        return _Result(0)


class _SetupEnvironment:
    task_env_config = SimpleNamespace(workdir="/app")

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def upload_dir(self, source, destination) -> None:
        del source, destination

    async def exec(self, command: str, **kwargs) -> _Result:
        del kwargs
        self.calls.append(command)
        # The first call is the prebuilt-runtime probe; force the fallback.
        return _Result(1 if len(self.calls) == 1 else 0)


def _agent() -> RolloHarborAgent:
    agent = object.__new__(RolloHarborAgent)
    agent.model_name = "deepseek-v4-flash"
    agent._get_cli_model = lambda: "deepseek-v4-flash"
    agent._runtime_env = lambda: {}
    agent._logged_command = lambda command, **kwargs: command
    return agent


def test_usage_is_recorded_when_environment_exec_is_cancelled():
    agent = _agent()
    environment = _Environment(cancelled=True)
    context = AgentContext()

    async def exercise() -> None:
        with pytest.raises(asyncio.CancelledError):
            await agent.run("test", environment, context)

    asyncio.run(exercise())

    assert context.n_input_tokens == 30
    assert context.n_cache_tokens == 20
    assert context.n_output_tokens == 3
    assert context.metadata is not None
    assert context.metadata["usage_available"] is True
    assert context.metadata["return_code"] is None


def test_harbor_timeout_preserves_timeout_error_after_usage_collection():
    agent = _agent()
    environment = _Environment(cancelled=False, delay_first=True)
    context = AgentContext()

    async def exercise() -> None:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(agent.run("test", environment, context), timeout=0.01)

    asyncio.run(exercise())

    assert context.n_input_tokens == 30
    assert context.n_cache_tokens == 20
    assert context.n_output_tokens == 3


def test_usage_is_recorded_before_a_nonzero_agent_exit_is_raised():
    agent = _agent()
    environment = _Environment(cancelled=False)
    context = AgentContext()

    original_exec = environment.exec

    async def failed_agent_exec(command: str, **kwargs) -> _Result:
        if " -u -m rollo " in command:
            await original_exec(command, **kwargs)
            return _Result(1, stderr="agent failed")
        return await original_exec(command, **kwargs)

    environment.exec = failed_agent_exec

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="Rollo Code exited with code 1"):
            await agent.run("test", environment, context)

    asyncio.run(exercise())

    assert context.n_input_tokens == 30
    assert context.n_cache_tokens == 20
    assert context.n_output_tokens == 3


def test_setup_uses_configured_apt_and_pip_mirrors():
    agent = _agent()
    settings = {
        "ROLLO_APT_MIRROR": "https://mirrors.tuna.tsinghua.edu.cn/ubuntu/",
        "ROLLO_PIP_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple",
    }
    agent._get_setting = settings.get
    environment = _SetupEnvironment()

    asyncio.run(agent.setup(environment))

    bootstrap = environment.calls[1]
    pip_install = environment.calls[2]
    assert "https://mirrors.tuna.tsinghua.edu.cn/ubuntu" in bootstrap
    assert "python3 -c 'import ensurepip'" in bootstrap
    assert "Acquire::Retries=3" in bootstrap
    assert "--index-url https://pypi.tuna.tsinghua.edu.cn/simple" in pip_install
    assert "--retries 3 --timeout 30" in pip_install
