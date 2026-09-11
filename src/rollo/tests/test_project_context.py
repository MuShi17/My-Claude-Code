"""ProjectContext 与测试环境隔离的验证（C01）。

覆盖 spec `project-context` 的等价类归一、拒绝输入、不可变性、memory 目录
冻结，以及测试进程的环境隔离守卫（dotenv / 临时数据根）。

注意：环境隔离守卫必须放在**被 pytest 收集的测试模块**里；放在 conftest.py
中的 test 函数不会被收集（首轮实现曾因此成为死代码，见评审 N2）。

用例 docstring 的场景标签约定为 `R<requirement 序号>-S<该 requirement 内序号>`
（与 `specs/project-context/spec.md` 的小节顺序一一对应），不使用全局场景序号。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import pytest

from rollo.project_context import (
    AmbiguousWorkspaceError,
    ProjectContext,
    ProjectContextError,
    WorkspaceNotFoundError,
    require_context,
    resolve_workspace_root,
    workspace_id_for,
)


# ─── 环境隔离守卫 ──────────────────────────────────────────────


def test_dotenv_is_disabled_for_test_runs() -> None:
    """测试进程不得因仓库根 .env 而持有真实密钥。

    python-dotenv 1.2+ 才会读取 PYTHON_DOTENV_DISABLED；旧版本下本断言失败，
    使"隔离失效"成为显式红灯，而不是静默读入真实密钥。本用例必须位于被
    pytest 收集的模块中——放在 conftest.py 里的 test 函数永远不会被执行。
    """

    from dotenv import load_dotenv

    assert os.environ.get("PYTHON_DOTENV_DISABLED") == "1"
    assert load_dotenv() is False, "python-dotenv 未识别 PYTHON_DOTENV_DISABLED（需 >=1.2.0）"
    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ROLLO_MODEL",
        "ROLLO_THINKING_EFFORT",
    ):
        assert key not in os.environ, f"测试进程不应带有来自 .env 的 {key}"


def test_entry_reports_process_cwd_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """入口失败契约（GAP-I03-11）：cwd 不可用时给可诊断错误，而非裸 OSError。

    输入面说明：CLI 目前没有 workspace 参数（D7 保持「当前目录即 workspace」），
    因此本平台可稳定构造的失败输入面只有「进程当前目录不可用」。这里用真实的
    `Path.cwd()` 失败（monkeypatch 触发真实抛错）验证行为，而不是 mock 解析逻辑。
    """

    def _boom() -> Path:
        raise FileNotFoundError(2, "The system cannot find the path specified")

    monkeypatch.setattr(Path, "cwd", staticmethod(_boom))
    with pytest.raises(ProjectContextError) as excinfo:
        ProjectContext.from_root(None)
    message = str(excinfo.value)
    assert "当前目录" in message
    assert not isinstance(excinfo.value, FileNotFoundError)


def test_scope_exceeding_spelling_does_not_raise(workspace_root: Path) -> None:
    """R1-S3：等价类外的写法不因"形式不在类内"被拒绝。

    用真实的 Windows 扩展长度（``\\\\?\\``）写法——它是等价类明确不保证的形式，
    但仍必须可解析、可产生身份，且不抛错。
    """

    if os.name != "nt":
        pytest.skip("扩展长度前缀是 Windows 语义")
    extended = _extended_prefix_path(workspace_root)
    resolved = resolve_workspace_root(extended)
    assert resolved.is_dir()
    assert workspace_id_for(resolved)


def test_must_exist_false_allows_absent_root(tmp_path: Path) -> None:
    """R2 旁路：`must_exist=False` 只用于创建前解析身份，不得被入口使用。"""

    absent = tmp_path / "not-created-yet"
    with pytest.raises(WorkspaceNotFoundError):
        ProjectContext.from_root(absent)
    context = ProjectContext.from_root(absent, must_exist=False)
    assert context.root == Path(os.path.realpath(str(absent)))
    assert not absent.exists()


def test_isolated_data_root_is_temporary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """测试内的 session 数据根必须落在临时目录，且不被真实 HOME 影响。"""

    import rollo.session as session_module

    data_dir = Path(os.environ["ROLLO_RUNTIME_DIR"])
    assert data_dir.is_dir()
    assert data_dir.is_relative_to(tmp_path.parent)
    assert session_module.SESSION_DIR == data_dir / "sessions"

    monkeypatch.chdir(tmp_path)
    context = ProjectContext.from_root(tmp_path)
    assert context.runtime_data_dir == data_dir
    assert context.resolve_memory_dir().is_relative_to(data_dir)


# ─── 等价类归一 ────────────────────────────────────────────────


def test_equivalent_spellings_share_identity(workspace_root: Path) -> None:
    """大小写、尾分隔符与 `..` 段归一到同一根与同一身份。"""

    (workspace_root / "sub").mkdir()
    spellings = [
        workspace_root,
        Path(str(workspace_root) + os.sep),
        workspace_root / "sub" / "..",
        Path(str(workspace_root).swapcase()) if os.name == "nt" else workspace_root,
    ]
    roots = [resolve_workspace_root(s) for s in spellings]
    identities = {workspace_id_for(r) for r in roots}
    assert len(set(roots)) == 1
    assert len(identities) == 1


def _extended_prefix_path(path: Path) -> str:
    """构造 Windows 扩展长度（``\\\\?\\``）写法；非 Windows 返回原路径。"""

    if os.name != "nt":
        return str(path)
    return "\\\\?\\" + os.path.abspath(str(path))


def test_identity_matches_independent_legacy_hash(workspace_root: Path) -> None:
    """R5-S1：身份必须与**独立实现**的历史算法一致。

    鉴别性要求：期望值不调用被测的 `workspace_id_for`，而是独立按历史算法
    `sha256(os.path.realpath(...))[:16]` 复算（禁止自指断言）。

    **可搬迁性**：不硬编码任何绝对路径的期望摘要——那会让套件在副本/其他 worktree
    下必红（GAP-C02-16）。迁移性由"同一路径的不同写法哈希相同"保证。
    """

    legacy_hash = hashlib.sha256(
        os.path.realpath(os.fspath(workspace_root)).encode("utf-8")
    ).hexdigest()[:16]
    context = ProjectContext.from_root(workspace_root)
    assert context.workspace_id == legacy_hash

    # 负向绑定：身份 MUST NOT 依赖传入写法的大小写（否则同一目录会分裂成两个身份）。
    execution_root = Path(__file__).resolve().parents[3]
    upper = Path(str(execution_root).swapcase())
    assert workspace_id_for(execution_root) == workspace_id_for(upper)
    # 且与独立复算一致（不含硬编码摘要，可在任意路径下运行）
    assert workspace_id_for(execution_root) == hashlib.sha256(
        os.path.realpath(os.fspath(execution_root)).encode("utf-8")
    ).hexdigest()[:16]

    assert context.resolve_memory_dir() == (
        context.runtime_data_dir / "projects" / context.workspace_id / "memory"
    )


def test_identity_not_affected_by_process_cwd(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """身份不随进程当前目录变化。"""

    before = ProjectContext.from_root(workspace_root)
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    after = ProjectContext.from_root(workspace_root)
    assert (before.root, before.workspace_id) == (after.root, after.workspace_id)


# ─── 拒绝输入 ──────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["", "   ", "C:drive-relative", "bad\x00name"])
def test_ambiguous_inputs_are_rejected(value: str) -> None:
    if value == "C:drive-relative" and os.name != "nt":
        pytest.skip("驱动器相对路径是 Windows 语义")
    with pytest.raises(AmbiguousWorkspaceError):
        resolve_workspace_root(value)


def test_missing_root_is_rejected_and_never_created(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(WorkspaceNotFoundError):
        ProjectContext.from_root(missing)
    assert not missing.exists()


def test_require_context_fails_without_fallback() -> None:
    with pytest.raises(ProjectContextError):
        require_context(None)


# ─── 不可变与 memory 冻结 ──────────────────────────────────────


def test_construction_is_not_affected_by_later_cwd_change(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = ProjectContext.from_root(workspace_root)
    snapshot = (
        context.root,
        context.workspace_id,
        context.config_root,
        context.runtime_data_dir,
        context.tool_cwd,
        context.memory_root,
    )
    other = tmp_path / "later"
    other.mkdir()
    monkeypatch.chdir(other)
    assert snapshot == (
        context.root,
        context.workspace_id,
        context.config_root,
        context.runtime_data_dir,
        context.tool_cwd,
        context.memory_root,
    )


def test_explicit_data_root_wins_over_environment(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit-data"
    monkeypatch.setenv("ROLLO_RUNTIME_DIR", str(tmp_path / "env-data"))
    first = ProjectContext.from_root(workspace_root, runtime_data_dir=explicit)
    second = ProjectContext.from_root(workspace_root, runtime_data_dir=tmp_path / "other-data")
    assert first.runtime_data_dir == explicit
    assert first.runtime_data_dir != second.runtime_data_dir


def test_memory_dir_resolution_is_frozen(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """构造后即使其它 memory 根出现，解析结果也不改判（评审 N4）。"""

    context = ProjectContext.from_root(workspace_root)
    first = context.resolve_memory_dir()
    decoy = context.runtime_data_dir / "projects" / "0000000000000000" / "memory"
    decoy.mkdir(parents=True, exist_ok=True)
    assert context.resolve_memory_dir() == first
    assert first != decoy


# ─── 按 workspace 隔离的读取与缓存 ─────────────────────────────


def _make_skill(workspace: Path, name: str, description: str) -> None:
    skill_dir = workspace / ".rollo" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody {description}\n",
        encoding="utf-8",
    )


def _make_agent(workspace: Path, name: str, description: str) -> None:
    agents_dir = workspace / ".rollo" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nsystem {description}\n",
        encoding="utf-8",
    )


def test_project_skills_are_isolated_per_workspace(tmp_path: Path) -> None:
    """先 A 后 B 再回 A：三次结果分别对应各自 workspace，且缓存语义保留。"""

    from rollo.skills import discover_skills, reset_skill_cache

    ws_a = tmp_path / "ws-a"
    ws_b = tmp_path / "ws-b"
    ws_a.mkdir()
    ws_b.mkdir()
    _make_skill(ws_a, "shared", "from-A")
    _make_skill(ws_b, "shared", "from-B")
    ctx_a = ProjectContext.from_root(ws_a)
    ctx_b = ProjectContext.from_root(ws_b)

    reset_skill_cache()
    first_a = discover_skills(ctx_a)
    first_b = discover_skills(ctx_b)
    back_to_a = discover_skills(ctx_a)

    assert [s.description for s in first_a if s.name == "shared"] == ["from-A"]
    assert [s.description for s in first_b if s.name == "shared"] == ["from-B"]
    assert [s.description for s in back_to_a if s.name == "shared"] == ["from-A"]
    # 同一 context 连续读取必须命中同一缓存对象（既有 test_skills 契约）。
    assert first_a is back_to_a
    assert first_a is not first_b

    reset_skill_cache()
    assert discover_skills(ctx_a) is not first_a


def test_memory_dirs_are_isolated_per_workspace(tmp_path: Path) -> None:
    """两个 workspace 的 memory 目录互不相同，且挂在各自 workspace_id 下。"""

    from rollo.memory import get_memory_dir

    ws_a = tmp_path / "ws-a"
    ws_b = tmp_path / "ws-b"
    ws_a.mkdir()
    ws_b.mkdir()
    ctx_a = ProjectContext.from_root(ws_a)
    ctx_b = ProjectContext.from_root(ws_b)

    dir_a = get_memory_dir(ctx_a)
    dir_b = get_memory_dir(ctx_b)

    assert dir_a != dir_b
    assert dir_a == ctx_a.runtime_data_dir / "projects" / ctx_a.workspace_id / "memory"
    assert dir_b == ctx_b.runtime_data_dir / "projects" / ctx_b.workspace_id / "memory"


def test_two_contexts_interleaved_reads_do_not_cross(tmp_path: Path) -> None:
    """R4-S5：同一事件循环内交错读取，各自结果只含自身内容。

    顺序调用（A→B→A）不构成交错证据，这里在单个事件循环内以 `asyncio.gather`
    并发驱动（仓库未安装 pytest-asyncio，故在用例内显式 run 事件循环）。
    """

    from rollo.skills import discover_skills, reset_skill_cache

    ws_a = tmp_path / "ws-a"
    ws_b = tmp_path / "ws-b"
    ws_a.mkdir()
    ws_b.mkdir()
    _make_skill(ws_a, "shared", "from-A")
    _make_skill(ws_b, "shared", "from-B")
    ctx_a = ProjectContext.from_root(ws_a)
    ctx_b = ProjectContext.from_root(ws_b)
    reset_skill_cache()

    async def read(context: ProjectContext) -> list[str]:
        await asyncio.sleep(0)
        return [s.description for s in discover_skills(context) if s.name == "shared"]

    async def drive() -> list[list[str]]:
        return list(
            await asyncio.gather(read(ctx_a), read(ctx_b), read(ctx_a), read(ctx_b))
        )

    results = asyncio.run(drive())
    assert results == [["from-A"], ["from-B"], ["from-A"], ["from-B"]]


def test_default_path_leaves_no_cross_call_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4-S4：缺省（无 context）路径按调用点 cwd 解析，不残留上一次的状态。"""

    from rollo.skills import discover_skills, reset_skill_cache

    ws_a = tmp_path / "default-a"
    ws_b = tmp_path / "default-b"
    ws_a.mkdir()
    ws_b.mkdir()
    _make_skill(ws_a, "only-a", "A-default")
    _make_skill(ws_b, "only-b", "B-default")
    reset_skill_cache()

    monkeypatch.chdir(ws_a)
    names_a = {s.name for s in discover_skills()}
    reset_skill_cache()
    monkeypatch.chdir(ws_b)
    names_b = {s.name for s in discover_skills()}

    assert "only-a" in names_a and "only-b" not in names_a
    assert "only-b" in names_b and "only-a" not in names_b


def test_deleted_root_fails_diagnosably(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4-S6：构造后根被删除时，工具执行以可诊断错误失败，字段不变。"""

    import shutil

    from rollo.tools import _run_shell

    root = tmp_path / "gone"
    root.mkdir()
    context = ProjectContext.from_root(root)
    snapshot = (context.root, context.tool_cwd)
    shutil.rmtree(root)

    output = _run_shell({"command": "echo hi"}, context=context)
    assert output.startswith("Error:"), output
    assert (context.root, context.tool_cwd) == snapshot


def test_shell_runs_in_context_root(tmp_path: Path) -> None:
    """R8-S1：真实子进程在各自 context 根下落盘（不使用进程 cwd）。

    禁止用 mock subprocess 断言 `cwd=` 实参替代行为验证。
    """

    import sys

    from rollo.tools import _run_shell

    ws_a = tmp_path / "run-a"
    ws_b = tmp_path / "run-b"
    ws_a.mkdir()
    ws_b.mkdir()
    ctx_a = ProjectContext.from_root(ws_a)
    ctx_b = ProjectContext.from_root(ws_b)

    def write_marker(context: ProjectContext, name: str) -> str:
        script = f"import pathlib;pathlib.Path({name!r}).write_text('ok', encoding='utf-8')"
        return _run_shell(
            {"command": f'"{sys.executable}" -c "{script}"'},
            context=context,
        )

    out_a = write_marker(ctx_a, "marker.txt")
    out_b = write_marker(ctx_b, "marker.txt")
    assert "Error" not in out_a
    assert "Error" not in out_b
    assert (ws_a / "marker.txt").is_file()
    assert (ws_b / "marker.txt").is_file()


def test_tool_dispatch_uses_context_not_process_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R8 生产调度路径：经 `execute_tool_value` 分发的工具必须落在 context 根。

    这条用例针对的是**调度层**（handlers 分发 + context 传递），而不是直接调用
    底层 `_run_shell(..., context=...)` 的旁路；后者曾让 381 绿灯掩盖真实缺口。
    """

    from rollo.tools import execute_tool_value

    ws = tmp_path / "ctx-root"
    process_cwd = tmp_path / "proc-cwd"
    ws.mkdir()
    process_cwd.mkdir()
    (ws / "ctx-only.txt").write_text("alpha in ctx\n", encoding="utf-8")
    (process_cwd / "cwd-only.txt").write_text("alpha in cwd\n", encoding="utf-8")
    context = ProjectContext.from_root(ws)
    monkeypatch.chdir(process_cwd)

    listed = asyncio.run(
        execute_tool_value("list_files", {"path": ".", "pattern": "*.txt"}, {}, context=context)
    )
    grepped = asyncio.run(
        execute_tool_value("grep_search", {"pattern": "alpha", "path": "."}, {}, context=context)
    )

    assert "ctx-only.txt" in listed
    assert "cwd-only.txt" not in listed
    assert "ctx-only.txt" in grepped
    assert "cwd-only.txt" not in grepped


def test_file_tools_resolve_relative_paths_against_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R8：文件三工具（read/write/edit）的相对路径以 context 根解析。"""

    from rollo.tools import execute_tool

    ws = tmp_path / "files-root"
    process_cwd = tmp_path / "files-cwd"
    ws.mkdir()
    process_cwd.mkdir()
    (ws / "target.txt").write_text("original\n", encoding="utf-8")
    context = ProjectContext.from_root(ws)
    monkeypatch.chdir(process_cwd)
    state: dict[str, float] = {}

    # 经公开的 execute_tool 包装（含先读后改状态提交）；用 acceptEdits 模式，
    # 使本用例聚焦"相对路径以 context 为基准"，权限判定由 test_interaction_safety 覆盖。
    written = asyncio.run(
        execute_tool(
            "write_file", {"file_path": "created.txt", "content": "hello\n"}, state,
            context=context, mode="acceptEdits",
        )
    )
    read_back = asyncio.run(
        execute_tool("read_file", {"file_path": "created.txt"}, state, context=context)
    )
    edited = asyncio.run(
        execute_tool(
            "edit_file",
            {"file_path": "created.txt", "old_string": "hello", "new_string": "changed"},
            state,
            context=context,
            mode="acceptEdits",
        )
    )

    assert "created.txt" in written
    assert not (process_cwd / "created.txt").exists()
    assert "hello" in read_back
    assert "changed" in edited
    assert (ws / "created.txt").read_text(encoding="utf-8").strip() == "changed"
    assert "original" in (ws / "target.txt").read_text(encoding="utf-8")


def test_permission_rules_come_from_context_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R8/D1：项目权限规则必须从 context 的 workspace 读取，而非进程 cwd。"""

    from rollo.tools import check_permission, load_permission_rules, reset_permission_cache

    ws = tmp_path / "perm-root"
    process_cwd = tmp_path / "perm-cwd"
    ws.mkdir()
    process_cwd.mkdir()
    (ws / ".rollo").mkdir()
    (ws / ".rollo" / "settings.json").write_text(
        '{"permissions": {"deny": ["run_shell(rm -rf *)"]}}',
        encoding="utf-8",
    )
    context = ProjectContext.from_root(ws)
    monkeypatch.chdir(process_cwd)

    reset_permission_cache()
    # 用「危险命令 + deny 规则」构造有鉴别力的判定：不带 context 时项目 deny
    # 规则不可见，结果会不同（allow/confirm），从而真正证伪接线缺失。
    deny_decision = check_permission(
        "run_shell", {"command": "rm -rf /"}, "default", None, context=context
    )
    assert deny_decision["action"] == "deny"
    assert "Denied by permission rule" in deny_decision.get("message", "")
    assert "run_shell(rm -rf *)" in [
        f"{r['tool']}({r['pattern']})" for r in load_permission_rules(context)["deny"]
    ]


def test_new_file_confirmation_uses_context_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R8：default 模式下「是否新建文件」的判定必须基于 context 根。

    已存在于 context 根的文件不应触发新建确认；仅存在于进程 cwd 的同名文件
    不得让判定误以为文件已存在。
    """

    from rollo.tools import check_permission, reset_permission_cache

    ws = tmp_path / "confirm-root"
    process_cwd = tmp_path / "confirm-cwd"
    ws.mkdir()
    process_cwd.mkdir()
    (ws / "existing.txt").write_text("x\n", encoding="utf-8")
    (process_cwd / "only-in-cwd.txt").write_text("y\n", encoding="utf-8")
    context = ProjectContext.from_root(ws)
    monkeypatch.chdir(process_cwd)
    reset_permission_cache()

    existing = check_permission(
        "write_file", {"file_path": "existing.txt", "content": "z"}, "default", None, context=context
    )
    cwd_only = check_permission(
        "write_file",
        {"file_path": "only-in-cwd.txt", "content": "z"},
        "default",
        None,
        context=context,
    )

    # context 根下已存在 → 无需新建确认
    assert existing["action"] == "allow"
    # 只在进程 cwd 存在、context 根不存在 → 视为新建，需要确认
    assert cwd_only["action"] == "confirm"


def test_skill_fork_subagent_inherits_parent_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R8-S2：skill-fork 子 Agent 必须继承父 workspace 上下文。

    用哨兵异常捕获子 Agent 构造参数（而不是启动真实模型调用）。
    """

    import rollo.agent as agent_module
    from rollo.agent import Agent

    ws = tmp_path / "skill-root"
    process_cwd = tmp_path / "skill-cwd"
    ws.mkdir()
    process_cwd.mkdir()
    skill_dir = ws / ".rollo" / "skills" / "forked"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: forked\ndescription: fork skill\ncontext: fork\n---\nbody\n",
        encoding="utf-8",
    )
    context = ProjectContext.from_root(ws)
    monkeypatch.chdir(process_cwd)

    captured: dict[str, object] = {}

    class _CapturingAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("parent-sentinel")

    monkeypatch.setattr(agent_module, "Agent", _CapturingAgent)
    parent = Agent(project_context=context)
    monkeypatch.setattr(parent, "_record_sub_agent_event", lambda **kwargs: None)
    monkeypatch.setattr(
        agent_module, "print_sub_agent_start", lambda *a, **k: None, raising=False
    )
    monkeypatch.setattr(
        agent_module, "print_sub_agent_end", lambda *a, **k: None, raising=False
    )

    outcome = asyncio.run(parent._execute_skill_tool({"skill_name": "forked", "args": "task"}))

    assert "parent-sentinel" in outcome
    child_context = captured.get("project_context")
    assert child_context is not None, "skill-fork 子 Agent 未收到 project_context"
    assert child_context.root == context.root
    child_run_id = captured.get("runtime_run_id")
    assert isinstance(child_run_id, str) and child_run_id
    assert child_run_id.startswith(f"run-{parent.session_id}-skill-")


def test_workspace_rules_are_loaded_from_context_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R7：`.rollo/rules/*.md` 必须从 context 根加载，且与进程 cwd 无关。

    这条用例的存在是必需的：rules 加载曾被本 Change 的接线改坏（双重拼接
    `.rollo/rules/.rollo/rules`），而当时测试集没有任何 rules 覆盖，因此
    394 全绿也没能发现——属于「接线回归只能靠真实断言发现」的典型例子。
    """

    from rollo.prompt import build_system_prompt, load_project_instructions

    ws = tmp_path / "rules-root"
    process_cwd = tmp_path / "rules-cwd"
    (ws / ".rollo" / "rules").mkdir(parents=True)
    process_cwd.mkdir()
    (ws / ".rollo" / "rules" / "team.md").write_text(
        "RULE-MARKER: always use tabs\n", encoding="utf-8"
    )
    context = ProjectContext.from_root(ws)
    monkeypatch.chdir(process_cwd)

    project_instructions = load_project_instructions(context)
    prompt = build_system_prompt(context)

    assert "RULE-MARKER" in project_instructions
    assert "RULE-MARKER" in prompt
    assert str(ws) in prompt or str(ws) in project_instructions


def test_memory_index_is_written_into_context_memory_dir(tmp_path: Path) -> None:
    """R5/R8：经 context 写入记忆文件后，MEMORY.md 索引必须落在 context 记忆目录。

    鉴别力要点（两个变异都必须让它红）：
    - 去掉 `_auto_update_memory_index(..., context=...)` 的透传 → 索引落错目录；
    - 去掉 `_write_file` 的 context 相对路径解析 → 相对路径按进程 cwd 解析，写不到记忆目录。
    因此这里把数据根设为 workspace 本身（memory = `<ws>/projects/<id>/memory`，位于
    `context.tool_cwd` 之内），并用**相对路径**给出记忆文件路径。
    """

    import os as _os

    from rollo.memory import get_memory_dir
    from rollo.tools import _write_file

    ws = tmp_path / "mem-ws"
    process_cwd = tmp_path / "mem-cwd"
    ws.mkdir()
    process_cwd.mkdir()
    context = ProjectContext.from_root(ws, runtime_data_dir=ws)
    memory_dir = get_memory_dir(context)
    assert memory_dir.is_relative_to(context.tool_cwd)
    relative_target = _os.path.relpath(memory_dir / "project_demo.md", context.tool_cwd)
    assert not _os.path.isabs(relative_target)

    previous = _os.getcwd()
    _os.chdir(process_cwd)
    try:
        _write_file(
            {
                "file_path": relative_target,
                "content": "---\nname: demo\ndescription: demo memory\ntype: project\n---\nbody\n",
            },
            context=context,
        )
    finally:
        _os.chdir(previous)

    index = memory_dir / "MEMORY.md"
    assert index.is_file(), "context 记忆目录下未生成 MEMORY.md 索引"
    assert "demo" in index.read_text(encoding="utf-8")
    fallback = ProjectContext.from_root(process_cwd).resolve_memory_dir()
    assert fallback != memory_dir
    assert not (fallback / "MEMORY.md").exists()


def test_permission_rule_path_matching_is_basis_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R8：文件类权限规则在「规则用相对路径、调用用绝对路径」时仍须匹配。

    只有两侧基准不同时该断言才具备鉴别力（同基准比较在旧实现下也会通过）。
    """

    from rollo.tools import check_permission, reset_permission_cache

    ws = tmp_path / "rule-basis-root"
    process_cwd = tmp_path / "rule-basis-cwd"
    ws.mkdir()
    process_cwd.mkdir()
    (ws / ".rollo").mkdir()
    (ws / ".rollo" / "settings.json").write_text(
        '{"permissions": {"deny": ["write_file(secret.txt)"]}}',
        encoding="utf-8",
    )
    context = ProjectContext.from_root(ws)
    monkeypatch.chdir(process_cwd)
    reset_permission_cache()

    denied = check_permission(
        "write_file",
        {"file_path": str(ws / "secret.txt"), "content": "x"},
        "default",
        None,
        context=context,
    )
    assert denied["action"] == "deny"

    outside = check_permission(
        "write_file",
        {"file_path": str(tmp_path / "elsewhere" / "secret.txt"), "content": "x"},
        "default",
        None,
        context=context,
    )
    assert outside["action"] != "deny"


def test_permission_rule_path_matching_uses_context_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R8：文件类权限规则里的相对路径与调用参数必须同基准（context 根）。"""

    from rollo.tools import check_permission, reset_permission_cache

    ws = tmp_path / "rule-root"
    process_cwd = tmp_path / "rule-cwd"
    ws.mkdir()
    process_cwd.mkdir()
    (ws / ".rollo").mkdir()
    (ws / ".rollo" / "settings.json").write_text(
        '{"permissions": {"deny": ["write_file(secret.txt)"]}}',
        encoding="utf-8",
    )
    context = ProjectContext.from_root(ws)
    monkeypatch.chdir(process_cwd)
    reset_permission_cache()

    denied = check_permission(
        "write_file", {"file_path": "secret.txt", "content": "x"}, "default", None, context=context
    )
    assert denied["action"] == "deny"

    allowed = check_permission(
        "write_file", {"file_path": "other.txt", "content": "x"}, "default", None, context=context
    )
    assert allowed["action"] != "deny"


def test_agent_tool_subagent_inherits_parent_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R8-S2：普通 `agent` 工具分支同样必须让子 Agent 继承父 workspace。"""

    import rollo.agent as agent_module
    from rollo.agent import Agent

    ws = tmp_path / "agent-root"
    process_cwd = tmp_path / "agent-cwd"
    ws.mkdir()
    process_cwd.mkdir()
    context = ProjectContext.from_root(ws)
    monkeypatch.chdir(process_cwd)

    captured: dict[str, object] = {}

    class _CapturingAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("parent-sentinel")

    monkeypatch.setattr(agent_module, "Agent", _CapturingAgent)
    parent = Agent(project_context=context)
    monkeypatch.setattr(parent, "_record_sub_agent_event", lambda **kwargs: None)
    monkeypatch.setattr(
        agent_module, "print_sub_agent_start", lambda *a, **k: None, raising=False
    )
    monkeypatch.setattr(
        agent_module, "print_sub_agent_end", lambda *a, **k: None, raising=False
    )

    outcome = asyncio.run(
        parent._execute_agent_tool({"type": "explore", "description": "d", "prompt": "p"})
    )

    assert "parent-sentinel" in outcome
    child_context = captured.get("project_context")
    assert child_context is not None, "agent 工具子 Agent 未收到 project_context"
    assert child_context.root == context.root
    child_run_id = captured.get("runtime_run_id")
    assert isinstance(child_run_id, str) and child_run_id.startswith(
        f"run-{parent.session_id}-explore-"
    )


def test_cli_exits_with_diagnostic_when_workspace_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """R3-S1：入口在 workspace 解析失败时给出可读消息、非零退出码、无堆栈。"""

    import sys as _sys

    from rollo import __main__ as cli_module

    def _boom() -> Path:
        raise FileNotFoundError(2, "The system cannot find the path specified")

    monkeypatch.setattr(Path, "cwd", staticmethod(_boom))
    monkeypatch.setattr(_sys, "argv", ["rollo", "hello"])

    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()

    assert excinfo.value.code == 2
    assert "Cannot resolve workspace" in capsys.readouterr().out


def test_explicit_data_root_drives_memory_and_documents_session_gap(tmp_path: Path) -> None:
    """R6-S1：显式数据根决定 memory 位置；session/artifacts 根尚未由 context 派生。

    如实声明边界：本 Change 只统一了 memory 根；`session.SESSION_DIR` 仍是导入期
    常量、artifacts 根仍由 `runtime_data_dir()` 直读环境（属 C03/C05 的输入面）。
    因此本用例**不**断言 session 侧已接线，只断言 memory 侧成立并记录该 gap。
    """

    import rollo.session as session_module

    data_root = tmp_path / "explicit-data"
    ws = tmp_path / "session-ws"
    ws.mkdir()
    context = ProjectContext.from_root(ws, runtime_data_dir=data_root)

    assert context.runtime_data_dir == data_root
    assert context.resolve_memory_dir().is_relative_to(data_root)
    assert context.resolve_memory_dir() == data_root / "projects" / context.workspace_id / "memory"

    # 记录的 gap：session 根与 context 无关，仍取导入期常量。
    # 这是跨 Change 的 tripwire：C03 一旦引入 `session_dir_for_context` 之类的
    # workspace 数据根接线，本断言即会变红——届时应把它替换为正向断言而非删除。
    assert not hasattr(session_module, "session_dir_for_context")


def test_agents_are_isolated_per_workspace(tmp_path: Path) -> None:
    """R7-S1：项目 agents 定义按 workspace 隔离（skills 之外的另一半）。"""

    from rollo.subagent import _discover_custom_agents, reset_agent_cache

    ws_a = tmp_path / "ag-a"
    ws_b = tmp_path / "ag-b"
    ws_a.mkdir()
    ws_b.mkdir()
    _make_agent(ws_a, "shared-agent", "desc-A")
    _make_agent(ws_b, "shared-agent", "desc-B")
    ctx_a = ProjectContext.from_root(ws_a)
    ctx_b = ProjectContext.from_root(ws_b)

    reset_agent_cache()
    desc_a = _discover_custom_agents(ctx_a)["shared-agent"]["description"]
    desc_b = _discover_custom_agents(ctx_b)["shared-agent"]["description"]
    back_to_a = _discover_custom_agents(ctx_a)["shared-agent"]["description"]

    assert (desc_a, desc_b, back_to_a) == ("desc-A", "desc-B", "desc-A")


def test_changed_context_root_does_not_reuse_old_cache(tmp_path: Path) -> None:
    """R7-S4：context 根变化后不得回落到旧缓存。"""

    from rollo.skills import discover_skills, reset_skill_cache

    ws_a = tmp_path / "cache-a"
    ws_b = tmp_path / "cache-b"
    ws_a.mkdir()
    ws_b.mkdir()
    _make_skill(ws_a, "only-a", "A-cache")
    ctx_a = ProjectContext.from_root(ws_a)
    reset_skill_cache()
    names_a = {s.name for s in discover_skills(ctx_a)}
    assert "only-a" in names_a

    # workspace B 在 A 已被缓存之后新增定义：B 的结果不得来自 A 的缓存。
    _make_skill(ws_b, "only-b", "B-cache")
    ctx_b = ProjectContext.from_root(ws_b)
    names_b = {s.name for s in discover_skills(ctx_b)}
    assert "only-b" in names_b
    assert "only-a" not in names_b


def test_env_change_after_construction_is_declared_behaviour(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5-S3：环境变量属构造期输入——重新构造得到新数据根，但旧对象不变。"""

    first_root = tmp_path / "data-one"
    second_root = tmp_path / "data-two"
    monkeypatch.setenv("ROLLO_RUNTIME_DIR", str(first_root))
    first = ProjectContext.from_root(workspace_root)

    monkeypatch.setenv("ROLLO_RUNTIME_DIR", str(second_root))
    second = ProjectContext.from_root(workspace_root)

    assert first.runtime_data_dir == first_root
    assert second.runtime_data_dir == second_root
    # 已构造对象必须保持其构造期取值（冻结语义）。
    assert first.runtime_data_dir == first_root
    assert first.workspace_id == second.workspace_id
