"""C02 权限与保护回归用例（spec interactive-requests I4.1 / I4.2）。

要点：批准**不构成授权** —— 执行前必须重新校验真实参数、当前 deny 策略与
文件「先读后改」的 mtime 保护。禁止用 mock 掉 `check_permission` 的方式证明
"策略仍生效"（那是伪验证）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from rollo.interactions import (
    InteractionKind,
    InteractionReply,
    InteractionState,
    RecordingInteractionPort,
    digest_params,
)
from rollo.project_context import ProjectContext
from rollo.runtime_ports import RecordingOutputPort


def _workspace(tmp_path: Path, settings: str | None = None) -> ProjectContext:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    if settings is not None:
        (ws / ".rollo").mkdir(exist_ok=True)
        (ws / ".rollo" / "settings.json").write_text(settings, encoding="utf-8")
    return ProjectContext.from_root(ws)


def _anthropic_response(*blocks):
    return SimpleNamespace(
        content=list(blocks),
        usage=SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


def _anthropic_block(block_type: str, **fields):
    return SimpleNamespace(type=block_type, **fields)


def test_approval_does_not_bypass_deny_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I4.1：即使交互端口批准，命中当前 deny 规则的调用仍被拒绝。"""

    from rollo.tools import check_permission, reset_permission_cache

    context = _workspace(tmp_path, '{"permissions": {"deny": ["write_file(secret.txt)"]}}')
    process_cwd = tmp_path / "cwd"
    process_cwd.mkdir()
    monkeypatch.chdir(process_cwd)
    reset_permission_cache()

    # 端口"批准"了这次调用 —— 但策略判定不受它影响。
    port = RecordingInteractionPort([InteractionReply(request_id="", approved=True)])
    reply = asyncio.run(
        port.request(
            type("R", (), {
                "request_id": "req-1",
                "kind": InteractionKind.APPROVAL,
                "session_id": "s",
                "run_id": "run",
                "params_digest": digest_params({"file_path": "secret.txt"}),
                "tool_call_id": None,
                "tool_name": "write_file",
            })()
        )
    )
    assert reply.approved is True

    decision = check_permission(
        "write_file",
        {"file_path": "secret.txt", "content": "x"},
        "default",
        None,
        context=context,
    )
    assert decision["action"] == "deny"

    # 因果链闭合：策略拒绝 → 公开执行入口**在边界内拦截** → 目标文件从未产生。
    from rollo.tools import execute_tool

    outcome = asyncio.run(
        execute_tool(
            "write_file",
            {"file_path": "secret.txt", "content": "x"},
            {},
            context=context,
        )
    )
    assert "denied by permission policy" in outcome
    assert not (context.tool_cwd / "secret.txt").exists()

    # 对照：未被拒绝的路径（acceptEdits 模式）确实能写入，证明上面的否定不是"恒不写"。
    asyncio.run(
        execute_tool(
            "write_file",
            {"file_path": "public.txt", "content": "ok"},
            {},
            context=context,
            mode="acceptEdits",
        )
    )
    assert (context.tool_cwd / "public.txt").is_file()

    allowed = check_permission(
        "write_file",
        {"file_path": "public.txt", "content": "x"},
        "default",
        None,
        context=context,
    )
    assert allowed["action"] != "deny"


def test_approval_does_not_bypass_read_before_edit_mtime_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I4.2：批准后目标文件被外部修改时，先读后改保护仍生效。"""

    from rollo.tools import execute_tool

    context = _workspace(tmp_path)
    process_cwd = tmp_path / "cwd"
    process_cwd.mkdir()
    monkeypatch.chdir(process_cwd)
    state: dict[str, float] = {}

    target = context.tool_cwd / "target.txt"
    target.write_text("original\n", encoding="utf-8")

    # 先读（登记 mtime），随后外部修改文件
    asyncio.run(execute_tool("read_file", {"file_path": "target.txt"}, state, context=context))
    time.sleep(0.01)
    target.write_text("changed externally\n", encoding="utf-8")
    os.utime(target, (time.time() + 5, time.time() + 5))

    edited = asyncio.run(
        execute_tool(
            "edit_file",
            {"file_path": "target.txt", "old_string": "original", "new_string": "hacked"},
            state,
            context=context,
        )
    )

    assert "Warning" in edited or "Error" in edited
    assert "hacked" not in target.read_text(encoding="utf-8")


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_public_agent_run_rechecks_policy_after_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """I4.1：批准返回后，真实双 Provider 路径仍重新校验当前策略。"""

    from rollo.agent import Agent

    context = _workspace(tmp_path)
    process_cwd = tmp_path / f"cwd-{provider}"
    process_cwd.mkdir()
    monkeypatch.chdir(process_cwd)
    target = context.tool_cwd / f"{provider}-must-not-write.txt"
    output = RecordingOutputPort()

    class _ApproveThenPlan(RecordingInteractionPort):
        agent: Agent | None = None

        async def request(self, request):
            reply = await super().request(request)
            assert reply.approved is True
            assert self.agent is not None
            self.agent.permission_mode = "plan"
            return reply

    interaction = _ApproveThenPlan(
        [InteractionReply(request_id="", approved=True)]
    )
    tool_input = {"file_path": str(target), "content": "must not write"}
    if provider == "anthropic":
        responses = iter(
            [
                _anthropic_response(
                    _anthropic_block(
                        "tool_use", id="call-policy", name="write_file", input=tool_input
                    )
                ),
                _anthropic_response(_anthropic_block("text", text="done")),
            ]
        )

        async def fake_call(self: Agent):
            return next(responses)

        monkeypatch.setattr(Agent, "_call_anthropic_stream", fake_call)
    else:
        responses = iter(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-policy",
                                        "type": "function",
                                        "function": {
                                            "name": "write_file",
                                            "arguments": json.dumps(tool_input),
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "done",
                                "reasoning_content": "",
                                "tool_calls": [],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            ]
        )

        async def fake_call(self: Agent):
            return next(responses)

        monkeypatch.setattr(Agent, "_call_openai_stream", fake_call)

    agent = Agent(
        api_base="https://fixture.invalid/v1" if provider == "openai" else None,
        api_key="fixture-key",
        model="fixture-model",
        thinking_effort="none",
        custom_system_prompt="fixture system",
        is_sub_agent=True,
        project_context=context,
        output_port=output,
        interaction_port=interaction,
    )
    interaction.agent = agent
    agent._mcp_initialized = True

    async def scenario() -> None:
        try:
            await agent.chat("write the file")
        finally:
            await agent.aclose()

    asyncio.run(scenario())

    assert target.exists() is False
    assert any(event.kind == "tool_denied" for event in output.events)
    assert any(event.tool_call_id == "call-policy" for event in output.events)


def test_public_agent_run_keeps_external_file_creation_blocked_after_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I4.2：批准等待期间外部创建目标文件时，真实 run 仍不覆盖它。"""

    from rollo.agent import Agent

    context = _workspace(tmp_path)
    process_cwd = tmp_path / "cwd-external"
    process_cwd.mkdir()
    monkeypatch.chdir(process_cwd)
    target = context.tool_cwd / "external.txt"
    output = RecordingOutputPort()

    class _ApproveThenCreate(RecordingInteractionPort):
        async def request(self, request):
            reply = await super().request(request)
            target.write_text("external content", encoding="utf-8")
            os.utime(target, (time.time() + 5, time.time() + 5))
            return reply

    interaction = _ApproveThenCreate(
        [InteractionReply(request_id="", approved=True)]
    )
    responses = iter(
        [
            _anthropic_response(
                _anthropic_block(
                    "tool_use",
                    id="call-external",
                    name="write_file",
                    input={"file_path": str(target), "content": "agent content"},
                )
            ),
            _anthropic_response(_anthropic_block("text", text="done")),
        ]
    )

    async def fake_call(self: Agent):
        return next(responses)

    monkeypatch.setattr(Agent, "_call_anthropic_stream", fake_call)
    agent = Agent(
        api_key="fixture-key",
        model="fixture-model",
        thinking_effort="none",
        custom_system_prompt="fixture system",
        is_sub_agent=True,
        project_context=context,
        output_port=output,
        interaction_port=interaction,
    )
    agent._mcp_initialized = True

    async def scenario() -> None:
        try:
            await agent.chat("write the file")
        finally:
            await agent.aclose()

    asyncio.run(scenario())

    assert target.read_text(encoding="utf-8") == "external content"
    assert any(
        event.kind == "tool_result" and "Error" in str(event.payload.get("result", ""))
        for event in output.events
    )


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_public_agent_run_rejects_existing_file_mtime_change_after_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """I4.2：public 双 Provider 路径批准后，既有文件外部修改仍阻断 edit。"""

    from rollo.agent import Agent

    context = _workspace(tmp_path)
    process_cwd = tmp_path / f"cwd-mtime-{provider}"
    process_cwd.mkdir()
    monkeypatch.chdir(process_cwd)
    target = context.tool_cwd / f"{provider}-existing.txt"
    target.write_text("original\n", encoding="utf-8")
    approval_marker = context.tool_cwd / f"{provider}-approval-marker.txt"
    output = RecordingOutputPort()

    class _ApproveThenModify(RecordingInteractionPort):
        async def request(self, request):
            reply = await super().request(request)
            if "approval-marker" in request.prompt:
                target.write_text("changed externally\n", encoding="utf-8")
                os.utime(target, (time.time() + 5, time.time() + 5))
            return reply

    interaction = _ApproveThenModify([InteractionReply(request_id="", approved=True)])
    edit_input = {
        "file_path": str(target),
        "old_string": "original",
        "new_string": "hacked",
    }

    if provider == "anthropic":
        responses = iter(
            [
                _anthropic_response(
                    _anthropic_block(
                        "tool_use",
                        id="call-read-before-edit",
                        name="read_file",
                        input={"file_path": str(target)},
                    )
                ),
                _anthropic_response(
                    _anthropic_block(
                        "tool_use",
                        id="call-approval-marker",
                        name="write_file",
                        input={"file_path": str(approval_marker), "content": "approved"},
                    )
                ),
                _anthropic_response(
                    _anthropic_block(
                        "tool_use",
                        id="call-mtime-edit",
                        name="edit_file",
                        input=edit_input,
                    )
                ),
                _anthropic_response(_anthropic_block("text", text="done")),
            ]
        )

        async def fake_call(self: Agent):
            return next(responses)

        monkeypatch.setattr(Agent, "_call_anthropic_stream", fake_call)
    else:
        def openai_tool(call_id: str, name: str, arguments: dict) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": call_id,
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

        responses = iter(
            [
                openai_tool(
                    "call-read-before-edit", "read_file", {"file_path": str(target)}
                ),
                openai_tool(
                    "call-approval-marker",
                    "write_file",
                    {"file_path": str(approval_marker), "content": "approved"},
                ),
                openai_tool("call-mtime-edit", "edit_file", edit_input),
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "done",
                                "reasoning_content": "",
                                "tool_calls": [],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            ]
        )

        async def fake_call(self: Agent):
            return next(responses)

        monkeypatch.setattr(Agent, "_call_openai_stream", fake_call)

    agent = Agent(
        api_base="https://fixture.invalid/v1" if provider == "openai" else None,
        api_key="fixture-key",
        model="fixture-model",
        thinking_effort="none",
        custom_system_prompt="fixture system",
        is_sub_agent=True,
        project_context=context,
        output_port=output,
        interaction_port=interaction,
    )
    agent._mcp_initialized = True

    async def scenario() -> None:
        try:
            await agent.chat("read then edit the file")
        finally:
            await agent.aclose()

    asyncio.run(scenario())

    assert target.read_text(encoding="utf-8") == "changed externally\n"
    assert approval_marker.read_text(encoding="utf-8") == "approved"
    assert any(
        event.kind == "tool_result"
        and event.tool_call_id == "call-mtime-edit"
        and (
            "Warning" in str(event.payload.get("result", ""))
            or "Error" in str(event.payload.get("result", ""))
        )
        for event in output.events
    )


def test_public_agent_approval_cancel_order_dispatches_at_most_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I3.3：真实 `Agent.chat()` 的两种到达顺序都至多 dispatch 一次工具。"""

    from rollo.agent import Agent
    from rollo.runtime_store import SQLiteRuntimeStore

    async def run(cancel_first: bool) -> tuple[bool, str, bool, int, int]:
        root = tmp_path / ("cancel-first" if cancel_first else "approve-first")
        root.mkdir()
        context = ProjectContext.from_root(root)
        store = SQLiteRuntimeStore(root / "runtime.sqlite")
        output = RecordingOutputPort()
        target = context.tool_cwd / "race.txt"

        class _RacePort:
            name = "race"

            def __init__(self) -> None:
                self.entered = asyncio.Event()
                self.release = asyncio.Event()
                self.requests = []
                self.cancel_notifications = 0

            async def request(self, request):
                self.requests.append(request)
                self.entered.set()
                await self.release.wait()
                return InteractionReply(
                    request_id=request.request_id,
                    approved=True,
                    params_digest=request.params_digest,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    tool_call_id=request.tool_call_id,
                    tool_name=request.tool_name,
                    source=self.name,
                )

            def cancel_pending(self) -> None:
                self.cancel_notifications += 1

        port = _RacePort()
        responses = iter(
            [
                _anthropic_response(
                    _anthropic_block(
                        "tool_use",
                        id="call-race",
                        name="write_file",
                        input={"file_path": "race.txt", "content": "once"},
                    )
                ),
                _anthropic_response(_anthropic_block("text", text="done")),
            ]
        )

        async def fake_call(self: Agent):
            return next(responses)

        monkeypatch.setattr(Agent, "_call_anthropic_stream", fake_call)
        agent = Agent(
            api_key="fixture-key",
            model="fixture-model",
            thinking_effort="none",
            custom_system_prompt="fixture system",
            is_sub_agent=True,
            project_context=context,
            runtime_store=store,
            output_port=output,
            interaction_port=port,
        )
        agent._mcp_initialized = True
        dispatch_calls = []
        original_execute_tool_call = agent._execute_tool_call

        async def counted_execute_tool_call(name, inp):
            dispatch_calls.append((name, inp))
            return await original_execute_tool_call(name, inp)

        agent._execute_tool_call = counted_execute_tool_call
        chat_task = asyncio.create_task(agent.chat("write once"))
        await port.entered.wait()
        if cancel_first:
            cancelled = agent.cancel_pending_interactions()
            port.release.set()
        else:
            port.release.set()
            cancelled = []
        try:
            await chat_task
            request_id = port.requests[0].request_id
            return (
                target.exists(),
                agent.interaction_registry.state(request_id),
                bool(cancelled),
                len(dispatch_calls),
                len(output.of_kind("tool_call")),
            )
        finally:
            await agent.aclose()
            store.close()

    cancelled = asyncio.run(run(True))
    approved = asyncio.run(run(False))

    assert cancelled == (False, InteractionState.CANCELLED, True, 0, 1)
    assert approved == (True, InteractionState.RESOLVED, False, 1, 1)


def test_execute_tool_boundary_enforces_policy() -> None:
    """公开执行入口 `execute_tool` 必须**在边界内**强制权限策略。

    这是本轮由"批准不绕过策略"用例暴露的真实缺口：`execute_tool_value` 本身不做
    权限检查，任何直接调用 `execute_tool` 的路径都会绕过策略。修复后 deny 在边界
    内拦截、需要确认的调用在无交互端口时保守拒绝。
    """

    import tempfile

    from rollo.tools import check_permission, execute_tool, reset_permission_cache

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        ws.mkdir()
        (ws / ".rollo").mkdir()
        (ws / ".rollo" / "settings.json").write_text(
            '{"permissions": {"deny": ["run_shell(curl *)"]}}', encoding="utf-8"
        )
        context = ProjectContext.from_root(ws)
        reset_permission_cache()

        # deny 规则在边界内生效
        denied = asyncio.run(
            execute_tool("run_shell", {"command": "curl http://x"}, {}, context=context)
        )
        assert "denied by permission policy" in denied

        # 需要确认的危险命令：无交互端口时必须保守拒绝
        confirm_needed = check_permission(
            "run_shell", {"command": "rm -rf /tmp/x"}, "default", None, context=context
        )
        assert confirm_needed["action"] == "confirm"
        boundary = asyncio.run(
            execute_tool("run_shell", {"command": "rm -rf /tmp/x"}, {}, context=context)
        )
        assert "requires confirmation" in boundary

        # 只读工具仍可用（策略未过度收紧）
        ok = asyncio.run(execute_tool("list_files", {"path": ".", "pattern": "*"}, {}, context=context))
        assert "denied" not in ok.lower()

        # N-10 回归：**计划模式必须在公开边界生效**（此前 mode 未透传 → shell 被放行）
        planned = check_permission(
            "run_shell", {"command": "echo plan-path"}, "plan", None, context=context
        )
        assert planned["action"] == "deny"
        boundary_plan = asyncio.run(
            execute_tool(
                "run_shell", {"command": "echo plan-path"}, {}, context=context, mode="plan"
            )
        )
        assert "denied by permission policy" in boundary_plan
        assert "plan-path" not in boundary_plan


def test_plan_mode_still_denies_shell_after_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I4.1 补充：plan/dontAsk 等模式语义不因批准而改变。"""

    from rollo.tools import check_permission, reset_permission_cache

    context = _workspace(tmp_path)
    process_cwd = tmp_path / "cwd"
    process_cwd.mkdir()
    monkeypatch.chdir(process_cwd)
    reset_permission_cache()

    planned = check_permission("run_shell", {"command": "echo hi"}, "plan", None, context=context)
    assert planned["action"] == "deny"

    dont_ask = check_permission(
        "run_shell", {"command": "rm -rf /tmp/x"}, "dontAsk", None, context=context
    )
    assert dont_ask["action"] == "deny"
