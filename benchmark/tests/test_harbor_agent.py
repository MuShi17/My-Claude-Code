from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from harbor.models.agent.context import AgentContext

from benchmark.harbor_agent import MiniClaudeHarborAgent


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


def _agent() -> MiniClaudeHarborAgent:
    agent = object.__new__(MiniClaudeHarborAgent)
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
        if " -u -m mini_claude " in command:
            await original_exec(command, **kwargs)
            return _Result(1, stderr="agent failed")
        return await original_exec(command, **kwargs)

    environment.exec = failed_agent_exec

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="Mini Claude exited with code 1"):
            await agent.run("test", environment, context)

    asyncio.run(exercise())

    assert context.n_input_tokens == 30
    assert context.n_cache_tokens == 20
    assert context.n_output_tokens == 3
