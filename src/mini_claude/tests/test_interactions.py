"""C02 交互端口验证：身份绑定、状态机、幂等/冲突、过期与取消。

对应 spec `interactive-requests` 的 4 条 requirement / 11 个 Scenario。
断言对象是**状态与执行结果**，不是 UI 表现。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from mini_claude.interactions import (
    DenyingInteractionPort,
    InteractionKind,
    InteractionRegistry,
    InteractionReply,
    InteractionRequest,
    InteractionState,
    RecordingInteractionPort,
    ReplyConflictError,
    RequestExpiredError,
    RequestNotFoundOrClosedError,
    UnknownRequestError,
    digest_params,
)


def _approval(registry: InteractionRegistry, request_id: str = "req-1", **kwargs) -> InteractionRequest:
    params = kwargs.pop("params", {"command": "rm -rf /"})
    return registry.open(
        InteractionRequest(
            request_id=request_id,
            kind=InteractionKind.APPROVAL,
            session_id=kwargs.pop("session_id", "sess-1"),
            run_id=kwargs.pop("run_id", "run-1"),
            params_digest=digest_params(params),
            tool_call_id=kwargs.pop("tool_call_id", "call-1"),
            tool_name=kwargs.pop("tool_name", "run_shell"),
            **kwargs,
        )
    )


# ─── 身份与状态 ───────────────────────────────────────────────


def test_request_binds_identity_and_rejects_bad_kind() -> None:
    with pytest.raises(ValueError):
        InteractionRequest(
            request_id="r", kind="nope", session_id="s", run_id="run", params_digest="d"
        )
    with pytest.raises(ValueError):
        InteractionRequest(
            request_id="", kind=InteractionKind.APPROVAL, session_id="s", run_id="run", params_digest="d"
        )


def test_tampered_params_are_rejected_and_state_unchanged() -> None:
    registry = InteractionRegistry()
    _approval(registry)

    with pytest.raises(ReplyConflictError):
        registry.resolve(
            InteractionReply(request_id="req-1", approved=True, params_digest="deadbeef")
        )

    assert registry.state("req-1") == InteractionState.PENDING
    assert registry.reply_for("req-1") is None


def test_reply_for_unknown_request_fails() -> None:
    registry = InteractionRegistry()
    with pytest.raises(UnknownRequestError):
        registry.resolve(InteractionReply(request_id="missing", approved=True))


# ─── 幂等与冲突 ───────────────────────────────────────────────


def test_duplicate_identical_reply_is_idempotent() -> None:
    registry = InteractionRegistry()
    request = _approval(registry)
    first = registry.resolve(
        InteractionReply(request_id="req-1", approved=True, params_digest=request.params_digest)
    )
    second = registry.resolve(
        InteractionReply(request_id="req-1", approved=True, params_digest=request.params_digest)
    )

    assert second is first
    assert registry.state("req-1") == InteractionState.RESOLVED


def test_legacy_omitted_identity_reply_accepts_matching_explicit_retry() -> None:
    """旧端口省略身份后，带正确身份的重复回复仍保持幂等。"""

    registry = InteractionRegistry()
    request = _approval(registry)
    first = registry.resolve(InteractionReply(request_id=request.request_id, approved=True))

    second = registry.resolve(
        InteractionReply(
            request_id=request.request_id,
            approved=True,
            params_digest=request.params_digest,
            session_id=request.session_id,
            run_id=request.run_id,
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
        )
    )

    assert second is first
    with pytest.raises(ReplyConflictError):
        registry.resolve(
            InteractionReply(
                request_id=request.request_id,
                approved=True,
                params_digest="wrong-digest",
                session_id=request.session_id,
                run_id=request.run_id,
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
            )
        )


def test_conflicting_reply_is_rejected() -> None:
    registry = InteractionRegistry()
    request = _approval(registry)
    registry.resolve(
        InteractionReply(request_id="req-1", approved=True, params_digest=request.params_digest)
    )

    with pytest.raises(ReplyConflictError):
        registry.resolve(
            InteractionReply(request_id="req-1", approved=False, params_digest=request.params_digest)
        )
    assert registry.state("req-1") == InteractionState.RESOLVED


# ─── 过期与取消 ───────────────────────────────────────────────


def test_reply_after_expiry_is_rejected() -> None:
    registry = InteractionRegistry()
    request = _approval(registry, expires_at=time.monotonic() - 1)

    with pytest.raises(RequestExpiredError):
        registry.resolve(
            InteractionReply(request_id="req-1", approved=True, params_digest=request.params_digest)
        )
    assert registry.state("req-1") == InteractionState.EXPIRED

    # 再次回复：终态为 expired，仍必须拒绝
    with pytest.raises(RequestExpiredError):
        registry.resolve(
            InteractionReply(request_id="req-1", approved=True, params_digest=request.params_digest)
        )


def test_explicit_expire_and_cancel_close_single_direction() -> None:
    registry = InteractionRegistry()
    _approval(registry, request_id="req-expire")
    _approval(registry, request_id="req-cancel")

    registry.expire("req-expire")
    registry.cancel("req-cancel")

    assert registry.state("req-expire") == InteractionState.EXPIRED
    assert registry.state("req-cancel") == InteractionState.CANCELLED
    with pytest.raises(RequestNotFoundOrClosedError):
        registry.cancel("req-expire")  # 已终态，不可再转移


def test_cancel_all_clears_pending_requests() -> None:
    registry = InteractionRegistry()
    _approval(registry, request_id="a")
    _approval(registry, request_id="b")
    resolved = _approval(registry, request_id="c")
    registry.resolve(
        InteractionReply(request_id="c", approved=True, params_digest=resolved.params_digest)
    )

    cancelled = registry.cancel_all()

    assert sorted(cancelled) == ["a", "b"]
    assert registry.pending() == []
    # 已 resolved 的请求不被 cancel_all 改写
    assert registry.state("c") == InteractionState.RESOLVED


def test_two_pending_requests_keep_independent_bindings() -> None:
    """I1.3：两个同时 pending 的请求不能互相借用同摘要或工具身份。"""

    registry = InteractionRegistry()
    first = _approval(
        registry,
        request_id="pending-a",
        run_id="run-a",
        tool_call_id="call-a",
        params={"command": "rm -rf /tmp/a"},
    )
    second = _approval(
        registry,
        request_id="pending-b",
        run_id="run-b",
        tool_call_id="call-b",
        params={"command": "rm -rf /tmp/b"},
    )

    with pytest.raises(ReplyConflictError):
        registry.resolve(
            InteractionReply(
                request_id=first.request_id,
                approved=True,
                params_digest=first.params_digest,
                session_id=second.session_id,
                run_id=second.run_id,
                tool_call_id=second.tool_call_id,
                tool_name=second.tool_name,
            )
        )
    assert registry.state(first.request_id) == InteractionState.PENDING
    assert registry.state(second.request_id) == InteractionState.PENDING

    registry.resolve(
        InteractionReply(
            request_id=second.request_id,
            approved=True,
            params_digest=second.params_digest,
            session_id=second.session_id,
            run_id=second.run_id,
            tool_call_id=second.tool_call_id,
            tool_name=second.tool_name,
        )
    )
    assert registry.state(second.request_id) == InteractionState.RESOLVED

    registry.resolve(
        InteractionReply(
            request_id=first.request_id,
            approved=False,
            params_digest=first.params_digest,
            session_id=first.session_id,
            run_id=first.run_id,
            tool_call_id=first.tool_call_id,
            tool_name=first.tool_name,
        )
    )
    assert registry.state(first.request_id) == InteractionState.RESOLVED


# ─── 等待与端口语义 ───────────────────────────────────────────


def test_hold_port_wait_is_cancellable_without_blocking_control_plane() -> None:
    """等待人工输入必须是可取消的异步等待，取消后请求转 cancelled。"""

    registry = InteractionRegistry()
    port = RecordingInteractionPort()
    port.hold = True
    request = _approval(registry)

    async def scenario() -> str:
        task = asyncio.create_task(port.request(request))
        await asyncio.sleep(0)  # 让端口进入等待
        assert not task.done(), "端口应处于挂起等待状态"
        registry.cancel(request.request_id)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return registry.state(request.request_id)

    assert asyncio.run(scenario()) == InteractionState.CANCELLED


def test_denying_port_is_the_safe_default() -> None:
    """未注入交互端口时保守拒绝，且不读取终端。"""

    registry = InteractionRegistry()
    port = DenyingInteractionPort()
    request = _approval(registry)

    reply = asyncio.run(port.request(request))

    assert reply.approved is False
    assert reply.request_id == request.request_id
    assert len(port.requests) == 1


def test_cancel_pending_interactions_notifies_the_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-16：`Agent.cancel_pending_interactions()` 必须**通知端口**解除等待。

    此前只断言注册表状态，删掉 Agent→端口那一级通知仍然全绿。
    """

    from mini_claude.agent import Agent
    from mini_claude.project_context import ProjectContext

    class _CountingPort:
        name = "counting"

        def __init__(self) -> None:
            self.notified = 0
            self.requests: list[InteractionRequest] = []

        async def request(self, request: InteractionRequest) -> InteractionReply:
            self.requests.append(request)
            return InteractionReply(
                request_id=request.request_id, approved=False, source=self.name
            )

        def cancel_pending(self) -> None:
            self.notified += 1

    monkeypatch.chdir(tmp_path)
    port = _CountingPort()
    agent = Agent(
        project_context=ProjectContext.from_root(tmp_path), interaction_port=port
    )

    # 登记一个 pending 请求后再取消
    agent.interaction_registry.open(
        InteractionRequest(
            request_id="pending-1",
            kind=InteractionKind.APPROVAL,
            session_id="s",
            run_id="run",
            params_digest=digest_params("x"),
        )
    )
    cancelled = agent.cancel_pending_interactions()

    assert cancelled == ["pending-1"]
    assert port.notified == 1, "取消必须通知端口解除等待（N-16）"


def test_subagents_inherit_ports_when_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-14：显式提供端口时，子 Agent 必须继承（否则子 Agent 事件上游不可见）。"""

    from mini_claude.agent import Agent
    from mini_claude.project_context import ProjectContext
    from mini_claude.runtime_ports import RecordingOutputPort

    monkeypatch.chdir(tmp_path)
    port = RecordingOutputPort()
    agent = Agent(
        project_context=ProjectContext.from_root(tmp_path), output_port=port
    )

    # 记录子 Agent 构造参数（用哨兵异常避免真实运行）
    captured: dict[str, object] = {}

    class _Capture:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("child-sentinel")

    monkeypatch.setattr("mini_claude.agent.Agent", _Capture)
    monkeypatch.setattr(agent, "_record_sub_agent_event", lambda **kwargs: None)
    monkeypatch.setattr(
        "mini_claude.agent.print_sub_agent_start", lambda *a, **k: None, raising=False
    )
    monkeypatch.setattr(
        "mini_claude.agent.print_sub_agent_end", lambda *a, **k: None, raising=False
    )

    outcome = asyncio.run(
        agent._execute_agent_tool({"type": "explore", "description": "d", "prompt": "p"})
    )

    assert "child-sentinel" in outcome
    assert captured.get("output_port") is port, "子 Agent 未继承父输出端口"


def test_subagents_inherit_interaction_port_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-14 补充：子 Agent 也必须继承**交互端口**（此前只有 output_port 有守护）。"""

    from mini_claude.agent import Agent
    from mini_claude.project_context import ProjectContext

    monkeypatch.chdir(tmp_path)
    interaction = RecordingInteractionPort()
    agent = Agent(
        project_context=ProjectContext.from_root(tmp_path), interaction_port=interaction
    )

    captured: dict[str, object] = {}

    class _Capture:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("child-sentinel")

    monkeypatch.setattr("mini_claude.agent.Agent", _Capture)
    monkeypatch.setattr(agent, "_record_sub_agent_event", lambda **kwargs: None)
    monkeypatch.setattr(
        "mini_claude.agent.print_sub_agent_start", lambda *a, **k: None, raising=False
    )
    monkeypatch.setattr(
        "mini_claude.agent.print_sub_agent_end", lambda *a, **k: None, raising=False
    )

    outcome = asyncio.run(
        agent._execute_agent_tool({"type": "explore", "description": "d", "prompt": "p"})
    )

    assert "child-sentinel" in outcome
    assert captured.get("interaction_port") is interaction, "子 Agent 未继承父交互端口"


def test_skill_fork_inherits_both_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-14：skill-fork 也必须把两类端口传给子 Agent。"""

    from mini_claude.agent import Agent
    from mini_claude.project_context import ProjectContext
    from mini_claude.runtime_ports import RecordingOutputPort

    monkeypatch.chdir(tmp_path)
    output = RecordingOutputPort()
    interaction = RecordingInteractionPort()
    agent = Agent(
        project_context=ProjectContext.from_root(tmp_path),
        output_port=output,
        interaction_port=interaction,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "mini_claude.skills.execute_skill",
        lambda *args, **kwargs: {
            "context": "fork",
            "allowed_tools": [],
            "prompt": "fixture skill",
        },
    )

    class _Capture:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("skill-child-sentinel")

    monkeypatch.setattr("mini_claude.agent.Agent", _Capture)
    monkeypatch.setattr(agent, "_record_sub_agent_event", lambda **kwargs: None)

    outcome = asyncio.run(
        agent._execute_skill_tool({"skill_name": "fixture", "args": "run"})
    )

    assert "skill-child-sentinel" in outcome
    assert captured.get("output_port") is output
    assert captured.get("interaction_port") is interaction


def test_reply_from_another_run_or_tool_is_rejected() -> None:
    """I1.3：跨 run / 跨 tool_call 的回复无效，且不触发工具执行。

    实现层用显式 session/run/tool 身份与 `params_digest` 共同绑定"这次审批批的是什么"：
    即使另一 run/tool 复用同一摘要也必须被拒绝，状态不变。
    """

    registry = InteractionRegistry()
    dispatch_log: list[str] = []

    # run-A 的审批请求（工具调用 call-A）
    request_a = registry.open(
        InteractionRequest(
            request_id="a-1",
            kind=InteractionKind.APPROVAL,
            session_id="sess-1",
            run_id="run-A",
            tool_call_id="call-A",
            tool_name="run_shell",
            params_digest=digest_params({"command": "rm -rf /tmp/a"}),
        )
    )

    # 来自 run-B / 另一 tool_call 的回复；即使摘要相同也必须拒绝。
    foreign = InteractionReply(
        request_id="a-1",
        approved=True,
        params_digest=request_a.params_digest,
        source="run-B",
        session_id="sess-1",
        run_id="run-B",
        tool_call_id="call-B",
        tool_name="run_shell",
    )
    with pytest.raises(ReplyConflictError):
        registry.resolve(foreign)

    assert registry.state("a-1") == InteractionState.PENDING
    assert registry.reply_for("a-1") is None
    assert dispatch_log == []  # 未授权 → 无执行

    # 正确绑定的回复才被接受
    ok = registry.resolve(
        InteractionReply(
            request_id="a-1",
            approved=True,
            params_digest=request_a.params_digest,
            source="run-A",
        )
    )
    assert ok.request_id == "a-1"
    assert registry.state("a-1") == InteractionState.RESOLVED
    if ok.approved:
        dispatch_log.append(request_a.tool_call_id or "")
    assert dispatch_log == ["call-A"]


def test_approval_and_cancel_race_yields_single_result() -> None:
    """I3.3：审批与取消竞争时只产生一个确定结果，且工具至多执行一次。

    两种到达顺序都验证（状态机是**同步**方法，因此不存在交错窗口）：
    - 先 resolved：cancel 被拒（终态不可转移），授权保留一次；
    - 先 cancelled：回复被拒，不产生任何授权。
    """

    for first in ("approve", "cancel"):
        registry = InteractionRegistry()
        request = _approval(registry, request_id=f"race-{first}")
        dispatch_log: list[str] = []

        if first == "approve":
            registry.resolve(
                InteractionReply(
                    request_id=request.request_id,
                    approved=True,
                    params_digest=request.params_digest,
                )
            )
            with pytest.raises(RequestNotFoundOrClosedError):
                registry.cancel(request.request_id)
            assert registry.state(request.request_id) == InteractionState.RESOLVED
            # 授权恰好一次
            if registry.reply_for(request.request_id).approved:
                dispatch_log.append(request.request_id)
            assert dispatch_log == [request.request_id]
        else:
            registry.cancel(request.request_id)
            with pytest.raises(ReplyConflictError):
                registry.resolve(
                    InteractionReply(
                        request_id=request.request_id,
                        approved=True,
                        params_digest=request.params_digest,
                    )
                )
            assert registry.state(request.request_id) == InteractionState.CANCELLED
            assert registry.reply_for(request.request_id) is None
            assert dispatch_log == []  # 取消之后不得产生授权

        # 两种顺序下都只有一个终态
        assert registry.state(request.request_id) in InteractionState.TERMINAL


def test_agent_approval_cancel_race_is_barrierized() -> None:
    """I3.3：真实 Agent 等待与 cancel_pending 竞争时只产生一个终态。"""

    from mini_claude.agent import Agent
    from mini_claude.project_context import ProjectContext

    class _BarrierPort:
        name = "barrier"

        def __init__(self) -> None:
            self.requests: list[InteractionRequest] = []
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.cancel_notifications = 0

        async def request(self, request: InteractionRequest) -> InteractionReply:
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

    async def one(cancel_first: bool) -> tuple[bool, str, list[str], int]:
        port = _BarrierPort()
        agent = Agent(
            project_context=ProjectContext.from_root(Path.cwd()),
            interaction_port=port,
        )
        task = asyncio.create_task(agent._confirm_dangerous("rm -rf /"))
        await port.entered.wait()
        if cancel_first:
            cancelled = agent.cancel_pending_interactions()
            port.release.set()
        else:
            port.release.set()
            approved = await task
            cancelled = agent.cancel_pending_interactions()
            return approved, agent.interaction_registry.state(port.requests[0].request_id), cancelled, port.cancel_notifications
        approved = await task
        return approved, agent.interaction_registry.state(port.requests[0].request_id), cancelled, port.cancel_notifications

    cancel_result = asyncio.run(one(True))
    approve_result = asyncio.run(one(False))

    assert cancel_result[0] is False
    assert cancel_result[1] == InteractionState.CANCELLED
    assert cancel_result[2]
    assert cancel_result[3] == 1
    assert approve_result[0] is True
    assert approve_result[1] == InteractionState.RESOLVED
    assert approve_result[2] == []
    assert approve_result[3] == 1


def test_agent_approval_cancel_race_runs_respond_and_cancel_concurrently() -> None:
    """I3.3：respond 与 cancel 任务同时在屏障后放行，终态仍只有一个。"""

    from mini_claude.agent import Agent
    from mini_claude.project_context import ProjectContext

    class _ConcurrentPort:
        name = "concurrent"

        def __init__(self) -> None:
            self.requests: list[InteractionRequest] = []
            self.entered = asyncio.Event()
            self.reply_gate = asyncio.Event()
            self.cancel_notifications = 0

        async def request(self, request: InteractionRequest) -> InteractionReply:
            self.requests.append(request)
            self.entered.set()
            await self.reply_gate.wait()
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

    async def one(cancel_first: bool) -> tuple[bool, str, list[str], int]:
        port = _ConcurrentPort()
        agent = Agent(
            project_context=ProjectContext.from_root(Path.cwd()),
            interaction_port=port,
        )
        approval_task = asyncio.create_task(agent._confirm_dangerous("rm -rf /"))
        await port.entered.wait()

        cancel_gate = asyncio.Event()

        async def cancel_task() -> list[str]:
            await cancel_gate.wait()
            return agent.cancel_pending_interactions()

        cancel_job = asyncio.create_task(cancel_task())
        if cancel_first:
            cancel_gate.set()
            await asyncio.sleep(0)
            port.reply_gate.set()
        else:
            port.reply_gate.set()
            await asyncio.sleep(0)
            cancel_gate.set()

        approved, cancelled = await asyncio.gather(approval_task, cancel_job)
        request_id = port.requests[0].request_id
        return approved, agent.interaction_registry.state(request_id), cancelled, port.cancel_notifications

    cancel_result = asyncio.run(one(True))
    approve_result = asyncio.run(one(False))

    assert cancel_result[0] is False
    assert cancel_result[1] == InteractionState.CANCELLED
    assert cancel_result[2]
    assert cancel_result[3] == 1
    assert approve_result[0] is True
    assert approve_result[1] == InteractionState.RESOLVED
    assert approve_result[2] == []
    assert approve_result[3] == 1


def test_question_reply_is_not_a_tool_authorization() -> None:
    """I1.1：提问的回答只作为运行输入，不产生工具授权。

    鉴别性设计（原版是同义反复：`dispatched` 从未被写入、夹具从未参与）：
    - 用**同一个注册表**先落定一个 question 回复；
    - 断言该回复**不能**作为 approval 请求的回复被接受（不同 request_id）；
    - 且 question 的回复对象 `approved=False`，即使 answer 非空。
    """

    registry = InteractionRegistry()
    dispatch_log: list[str] = []

    question = registry.open(
        InteractionRequest(
            request_id="q-1",
            kind=InteractionKind.QUESTION,
            session_id="sess-1",
            run_id="run-1",
            params_digest=digest_params({"question": "which file?"}),
            prompt="which file?",
        )
    )
    approval = _approval(registry, request_id="a-1")

    answered = registry.resolve(
        InteractionReply(
            request_id="q-1",
            approved=False,
            answer="the first one",
            params_digest=question.params_digest,
        )
    )

    # 1) 回答存在，但不带授权语义
    assert answered.answer == "the first one"
    assert answered.approved is False

    # 2) 该回复不能被当作 approval 的回复：request_id 不匹配
    with pytest.raises(UnknownRequestError):
        registry.resolve(
            InteractionReply(
                request_id="q-1-typo",
                approved=True,
                params_digest=approval.params_digest,
            )
        )
    assert registry.state("a-1") == InteractionState.PENDING

    # 3) 审批必须由自己的回复落定；answered 对象本身不能被复用为授权凭据
    approved = registry.resolve(
        InteractionReply(
            request_id="a-1", approved=True, params_digest=approval.params_digest
        )
    )
    assert approved.approved is True
    assert approved.request_id == "a-1"
    assert answered.request_id != approved.request_id

    # 4) 只有 approval 的回复为真时才算授权（question 的回复永不进入该分支）
    authorized = answered.approved or approved.approved
    assert authorized is True
    if authorized:
        dispatch_log.append("a-1")
    assert dispatch_log == ["a-1"]  # 只有审批进入执行队列，question 不进入


# ─── Agent 接线（I2.3 / I3.x）──────────────────────────────────


def test_agent_confirm_does_not_fall_back_to_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I2.3：runtime 内不存在终端 input 回退；无端口时保守拒绝。"""

    from mini_claude.agent import Agent
    from mini_claude.project_context import ProjectContext

    def _boom(*args: object, **kwargs: object) -> str:
        raise AssertionError("runtime 不得读取终端 stdin")

    monkeypatch.setattr("builtins.input", _boom)
    monkeypatch.chdir(tmp_path)
    agent = Agent(project_context=ProjectContext.from_root(tmp_path))

    assert asyncio.run(agent._confirm_dangerous("rm -rf /")) is False
    assert isinstance(agent.interaction_port, DenyingInteractionPort)
    # 请求已被登记并终态化为拒绝
    states = [agent.interaction_registry.state(r.request_id) for r in agent.interaction_port.requests]
    assert states == [InteractionState.RESOLVED]


def test_agent_confirm_uses_injected_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I3.1：注入端口的批准被采纳；参数摘要与请求绑定一致。"""

    from mini_claude.agent import Agent
    from mini_claude.project_context import ProjectContext

    monkeypatch.chdir(tmp_path)
    port = RecordingInteractionPort(
        replies=[InteractionReply(request_id="", approved=True)]
    )
    agent = Agent(
        project_context=ProjectContext.from_root(tmp_path), interaction_port=port
    )

    assert asyncio.run(agent._confirm_dangerous("echo hi")) is True
    assert len(port.requests) == 1
    request = port.requests[0]
    assert request.kind == InteractionKind.APPROVAL
    assert request.params_digest == digest_params({"command": "echo hi"})
    assert agent.interaction_registry.state(request.request_id) == InteractionState.RESOLVED


def test_confirm_fn_remains_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """兼容入口：既有 confirm_fn 仍生效（不破坏既有调用方）。"""

    from mini_claude.agent import Agent
    from mini_claude.project_context import ProjectContext

    calls: list[str] = []

    async def _confirm(command: str) -> bool:
        calls.append(command)
        return True

    monkeypatch.chdir(tmp_path)
    agent = Agent(
        project_context=ProjectContext.from_root(tmp_path), confirm_fn=_confirm
    )

    assert asyncio.run(agent._confirm_dangerous("ls")) is True
    assert calls == ["ls"]
    # GAP-C02-19：兼容入口的结果也必须落定到注册表（不再绕过取消/幂等/失效判定）
    assert len(agent.interaction_registry.pending()) == 0
    resolved = [
        agent.interaction_registry.state(r.request_id)
        for r in agent.interaction_port.requests
    ]
    assert resolved == []  # 端口未被调用（confirm_fn 优先），但注册表已有一条 resolved
    states = [
        agent.interaction_registry.state(rid)
        for rid in list(agent.interaction_registry._requests)  # noqa: SLF001 - 断言内部登记
    ]
    assert states == [InteractionState.RESOLVED]


def test_cancel_pending_interactions_is_the_control_plane_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I2.1：取消入口存在，能把等待中的请求转为 cancelled。"""

    from mini_claude.agent import Agent
    from mini_claude.project_context import ProjectContext

    monkeypatch.chdir(tmp_path)
    port = RecordingInteractionPort()
    port.hold = True
    agent = Agent(
        project_context=ProjectContext.from_root(tmp_path), interaction_port=port
    )

    async def scenario() -> tuple[list[str], str]:
        task = asyncio.create_task(agent._confirm_dangerous("rm -rf /"))
        await asyncio.sleep(0)
        request_id = port.requests[0].request_id
        cancelled = agent.cancel_pending_interactions()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return cancelled, agent.interaction_registry.state(request_id)

    cancelled, state = asyncio.run(scenario())
    assert len(cancelled) == 1
    assert state == InteractionState.CANCELLED
