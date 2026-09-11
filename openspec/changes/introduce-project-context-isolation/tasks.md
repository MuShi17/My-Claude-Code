> 状态说明：本节 checklist 只反映**已按 D(C01) 门禁完成**的事项。D(C01) 尚未接受，
> 因此实现类任务即使已有产物也保持未勾选，并在 §6 逐条登记"已落盘但未获门禁通过"。

## 1. 变更准备与基线绑定

- [ ] 1.1 登记本 Change 的 actual_id、contract_revision 与 execution_root/branch/base（main@21b9bdf），确认唯一 writer=main-agent、无 mode_switch。
- [x] 1.2 复核前置门：`agent-harness project-verify` 返回 ok；C01 的 openspec_authorized 与 implementation_authorized 已登记为 true；GAP-I02-01 在本 Change 内闭合。
- [ ] 1.3 冻结并记录基线：`& $runtimePython -m pytest -q src/mini_claude/tests`，必须记录 runtimePython 绝对路径、Python 与 pytest 版本、执行目录、collected 数与 passed 数、warning 数，以及冻结时刻的工作树逐字 `git status --porcelain` 与全部新增文件 SHA256。**基线当前值：396 collected / 396 passed / 2 warning。**

#### 1.3 冻结基线快照（落盘，2026-09-10）

| 项 | 值 |
| --- | --- |
| runtimePython | `C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\python.exe` |
| Python / pytest | 3.14.6 / 9.1.1 |
| 执行目录 | `D:\workspace\My-Claude-Code`（仓库根） |
| 命令 | `& $runtimePython -m pytest -q src/mini_claude/tests` |
| 结果 | 396 collected / 396 passed / 2 warning；`openspec validate introduce-project-context-isolation --strict` → valid（exit 0；**注意**裸 `openspec validate --strict` 在非交互下会因"Nothing to validate"返回 exit 1，须带 change id）；`git diff --check` exit 0 |
| HEAD | `21b9bdfbb7c0bf3b2f2f47f13890724cff76a5bc`（branch=main，无提交/无推送） |

逐字 `git status --porcelain`：

```
 M AGENTS.md
 M src/mini_claude/__main__.py
 M src/mini_claude/agent.py
 M src/mini_claude/mcp_client.py
 M src/mini_claude/memory.py
 M src/mini_claude/prompt.py
 M src/mini_claude/skills.py
 M src/mini_claude/subagent.py
 M src/mini_claude/tools.py
 M src/pyproject.toml
?? openspec/changes/introduce-project-context-isolation/
?? src/mini_claude/project_context.py
?? src/mini_claude/tests/conftest.py
?? src/mini_claude/tests/test_project_context.py
```

SHA256（前 16，**闭合第四轮 A—D 判据缺陷后重冻**）：project_context.py `7D2C5AC18B907295`｜conftest.py `391F88EECEA419F1`｜test_project_context.py `7944364E9062D76C`｜tools.py `FD3E4F28B909F9BA`｜prompt.py `548066DDFDCC5BBB`｜agent.py `111AFB25C6663CD3`｜__main__.py `E75C016DE0DC2233`｜memory.py `FAFF598CBE2E302C`｜mcp_client.py `16E1E8B1D2E2D3DB`｜skills.py `32D35AF887EAB010`｜subagent.py `E58682197621D909`｜pyproject.toml `5AF20B72517167E6`｜AGENTS.md `AFC431B9306C0AE2`（**范围外**：项目契约 marker）｜spec.md `174927B12D53B25F`｜design.md `525D8494FF6C7AC1`｜proposal.md `FE172E3C3F6DA76E`。

> **本文件（tasks.md）的哈希不在表内**：任何写入都会改变自身摘要，硬编码必然自相矛盾。冻结时按 `Get-FileHash openspec/changes/introduce-project-context-isolation/tasks.md` 现算，并把结果记入 Item 页与台账（外部记录），不写回本文件。

（历史快照：test_project_context.py 曾为 `F2B7B692847CB291` / `060CB6DDC301786A`；本轮仅改测试 docstring 的 R 标签与文件头约定，未改产品行为。）

**V(C01) 差异审查约定**：以本快照为唯一基线；显式**排除** `AGENTS.md`（范围外的项目契约初始化）；`git diff`/`git diff --check` 不覆盖 3 个 untracked 文件（见 §5.9）。若闭合评审项导致再次写入，必须重新冻结本表。

- warning 判据（**按家族**，不按文件钉死）：允许家族为 `PytestUnraisableExceptionWarning`（asyncio proactor / subprocess transport 析构期 `ResourceWarning: unclosed transport` → `ValueError: I/O operation on closed pipe`）。**每次运行必须逐次记录 warning 数量与归属文件**，不得以家族豁免其它类型 warning。
- warning 归属**随集合组成漂移**（实测）：全量 = 2（`test_compaction_artifacts.py`）；5.8b 八文件 = 3（`test_cli_smoke.py`×2 + `test_recovery_resume.py`×1）。因此"某个固定文件是唯一来源"的说法不成立，已废弃。
- **既有性未证实（诚实登记）**：`design.md:11` 与本 Change 均未修改 `session.py`；但对同一 7 文件集合，工作树（改动后）出现该家族告警，而 HEAD 侧对照（`git archive` 快照）未出现。差异可能来自本 Change 的 conftest 隔离或源码接线，**尚未定性**。未执行 HEAD 全量基线（需要创建 git worktree，本批次未授权），因此**不得宣称"属既有、非本 Change 引入"**；V(C01) 应把它作为风险项处理，并记录每次运行的 warning 数量与归属。
- **flake（实测率约 2/7，非固定集合）**：`PermissionError: [WinError 5]` 出现在 `session.py:91` 的 `os.replace` 原子替换。已观测到的失败用例分别在**全量集合**（`test_archive_projection.py::test_agent_close_waits_for_active_provider_task_before_closing_owned_store`，8 次全量中 1 次）与 **5.8b 集合**（7 次中 2 次；`test_archive_projection.py` **不在**该集合内，说明是同一根因在不同集合上的表现）。**5.8b 那两次失败的 node ID 未记录到**（当时只捕获到错误类型与位置）——因此 V(C01) MUST NOT 据此预设具体 flake 用例，只能按错误类型（`WinError 5` @ `session.py` 的 `os.replace`）与重跑结果判定。`session.py` 的 blob 与 HEAD 逐字节相同，其**根因代码未被本 Change 改动**；但触发条件是否受本 Change 影响**尚未定性**（见上一条 warning 既有性说明）。**V(C01) 判据**：位置类失败必须核对错误类型是否为该 `WinError 5`，并以「同一用例重跑 3 次全绿」放行；不得以单次绿灯作为稳定判据。
- [ ] 1.4 产出并记录 C01 独立测试策略（含 case 清单、层级、mock 边界、Gap ID、结论与 GAP-I02-01 闭合证据），在主 Agent 接受 D(C01) 前通过独立评审。

## 2. 测试隔离基础设施（闭合 GAP-I02-01）

- [ ] 2.1 新增 `src/mini_claude/tests/conftest.py`：模块顶层设置 `PYTHON_DOTENV_DISABLED`（早于任何 mini_claude 导入），每个测试获得临时 HOME/USERPROFILE/`MINI_CLAUDE_RUNTIME_DIR`，并显式 monkeypatch 导入期常量 `session.SESSION_DIR`。
- [ ] 2.2 守卫测试：位于**被收集的测试模块**（`tests/test_project_context.py`，不是 conftest.py——conftest 中的 test 函数不会被 pytest 收集），断言 `PYTHON_DOTENV_DISABLED` 已强制置 1、`load_dotenv() is False`、且 `.env` 可能注入的全部键（API key/base URL/model/effort）都不在进程环境中。
- [ ] 2.3 依赖下限同步：`src/pyproject.toml` 的 `python-dotenv` 由 `>=1.0.1` 提到 `>=1.2.0`（该开关引入版本）。
- [ ] 2.4 改造既有依赖真实 HOME/cwd 的测试以复用隔离点；不新增产品侧配置开关。同时**重冻结基线**（conftest 会改变每个测试的环境，旧数字作废）。
- [ ] 2.5 统一数据根：确认测试内 session 数据根、runtime 数据根与 artifacts 根全部落在临时目录（`runtime_data_dir()` 为函数，必要时按调用期语义 patch）。
- [ ] 2.6 记录迁移声明：memory 根由固定 HOME 改为随 `runtime_data_dir`（有意统一 session/memory 数据根不一致），并在 spec/design 与回写中如实声明该行为变化。

## 3. ProjectContext 核心

- [ ] 3.1 新增 `src/mini_claude/project_context.py`：实现 `resolve_workspace_root()`（realpath 单一规范化来源、要求根存在）与四类失败（不存在、空输入、含 NUL、驱动器相对），并在进程当前目录不可读取时转换为可诊断的 ProjectContext 错误。
- [ ] 3.2 实现不可变 ProjectContext：`workspace_id`（realpath 派生，与历史取值一致）、`root`、`config_root`、`runtime_data_dir`、规则/技能/agents/MCP/memory 来源与工具 cwd 在构造时一次解析；不提供进程级可变当前 workspace，也不保留任何历史身份回退分支。
- [ ] 3.3 memory 目录在构造时**冻结**为字段，`resolve_memory_dir()` 为纯访问器（不重新判定文件系统），保证同一 workspace 只有一个 memory 根。
- [ ] 3.4 实现按 `workspace_id` 分键的派生缓存，替换模块级无键缓存，并保留既有 `reset_*_cache()` 语义与「同一 context 两次调用返回同一对象」契约。

## 4. 消费方接线

- [ ] 4.1 `tools.py`：项目 settings 与 `_cached_rules` 从 context 读取（`check_permission`/`_check_permission_rules` 均带 context）；`_run_shell` 显式传 `cwd=`；`list_files`/`grep_search` 与**文件三工具**（`read_file`/`write_file`/`edit_file`）的默认相对路径以 `context.tool_cwd` 解析（显式绝对路径优先）；`commit_tool_state` 的先读后改状态键同样以 context 解析；**调度路径必须把 context 传下去**——`execute_tool_value`/`execute_tool`/`commit_tool_state` 均带 keyword-only `context`，`agent.py` 调用点必须传 `self.context`（禁止只改底层函数而调度层回退 `Path.cwd()`）。
- [ ] 4.2 `mcp_client.py`：项目 `.claude/settings.json` 与 `.mcp.json` 从 context 读取；MCP 缓存按 context 分键。
- [ ] 4.3 `prompt.py`：CLAUDE.md 向上遍历、rules 目录、`{{cwd}}` 与 `get_git_context()` 的 `cwd=` 全部改用 context。
- [ ] 4.4 `skills.py`、`subagent.py`：项目 skills/agents 目录改用 context；`_cached_skills`、`_cached_custom_agents` 按 `workspace_id` 分键。
- [ ] 4.5 `memory.py`：memory 目录改用 `context.resolve_memory_dir()`（冻结字段访问器，禁止用静态字段另行推导建目录）。
- [ ] 4.6 `agent.py`：构造与传递 context；子 Agent 继承 workspace 上下文并保留自身 run/context 身份。
- [ ] 4.7 入口：`__main__.py` 与后续 worker 入口一次性构造 context 并注入；workspace 解析失败时按 D8 给出非零退出码与可读消息（MUST NOT 打印未处理堆栈）。
- [ ] 4.8 跨 Change 接口影响清单：列出本 Change 改动的消费方签名与本批次 C02—C04 需要重新确认的接线集合（任务卡 §9 要求），随 contract_handoff 一并交接。

## 5. 验证

- [ ] 5.1 `test_project_context.py`：覆盖等价类归一（大小写/尾分隔符/`..`，断言前双向 canonicalize 以防 8.3 短名假红）、身份不随 cwd 变化、身份与历史取值一致（鉴别性前置断言）、等价类外的真实绝对根不抛错、四类拒绝输入、不存在根**事后仍不存在**、构造后 cwd 变化不影响六个字段、`require_context(None)` 显式失败。
- [ ] 5.2 覆盖隔离场景，分列三种形态：①顺序「先 A 后 B 再回 A」（已落地）；②**真实交错**——两 context 的 `discover_skills` / `get_memory_dir` 以 `asyncio.gather` 或显式交替迭代驱动，断言各自结果只含自身内容（顺序调用不构成交错证据）；③同 context 两次同一对象 + reset 后不同对象 + context 根变化不回落旧缓存。
- [ ] 5.3 覆盖 memory：memory 目录等于 `<data_root>/projects/<workspace_id>/memory`；同一 context 多次调用结果恒定（其间在其它 projects 子目录建目录也不改判）；显式数据根决定 memory 根。**范围说明**：session 与 artifacts 数据根不在本 Change 统一范围（已写入 spec 的范围排除声明），因此本任务**不**断言它们由 context 派生；`test_explicit_data_root_drives_memory_and_documents_session_gap` 只记录该 gap，不计为对 session 侧的覆盖。
- [ ] 5.4 覆盖工具 cwd：两个 context 下以真实子进程（不 mock `subprocess`）执行相同相对路径写文件，断言产物分别落在各自根。禁止以"断言传了 `cwd=` 参数"替代行为验证。
- [ ] 5.5 覆盖子 Agent：`test_skill_fork_subagent_inherits_parent_context` 已用哨兵异常捕获子 Agent 构造参数，断言 `project_context.root == 父 context.root` 且子 `runtime_run_id` 带父 session 前缀；普通的 `agent` 工具分支（`_execute_agent_tool`）另需补一条同形态用例。
- [ ] 5.6 覆盖旧会话只读，**断言口径**（GAP-I03-14）：比较 `{相对路径: sha256(内容)}` 与路径集合，**排除** `session.v2.json` 的字节与全部 mtime（该文件在 load/resume 路径必然被原子重写，属既有显示缓存行为）；对 `session.v2.json` 只断言其派生字段（high-water、message count、projection/source digest）不变；run 数与 store high-water 不变；"未认领"以 A/B 两会话下 list 与 load 结果及磁盘摘要完全相同来证明。禁止用 mtime 证明"未改写"。**当前状态：未覆盖**——按本 Change 的 Non-Goals，workspace 绑定字段排在 C03 冻结，因此 R9-S1/S2 在此只能验证"既有行为未被本 Change 破坏"，完整认领语义应由 C03 承接；若把该用例留在 C01，须在 C03 变更准备时重新指定责任 Item。
- [ ] 5.7 覆盖入口失败契约：进程当前目录不可读取时以 ProjectContext 类错误失败（可诊断消息、非裸 `OSError`）；入口侧非零退出码与无堆栈由 4.7 接线后补验。
- [ ] 5.8a 既有集合（现在即可执行）：`& $runtimePython -m pytest -q src/mini_claude/tests`，期望 passed ≥ §1.3 冻结基线且无 failed/error。**warning 判据按家族**（`PytestUnraisableExceptionWarning`：asyncio proactor/subprocess transport 析构期告警），且**必须逐次记录数量与归属文件**；不得按"某个固定文件是唯一来源"豁免，也不得据此豁免其它家族。
- [ ] 5.8b 新增/聚焦集合（5.1—5.7 落地后执行）：`& $runtimePython -m pytest -q src/mini_claude/tests/test_project_context.py src/mini_claude/tests/test_skills.py src/mini_claude/tests/test_tool_result_boundary.py src/mini_claude/tests/test_canonical_event_fixtures.py src/mini_claude/tests/test_canonical_acceptance.py src/mini_claude/tests/test_cli_smoke.py src/mini_claude/tests/test_recovery_resume.py src/mini_claude/tests/test_local_consumers.py`，期望全绿。**该集合同样会命中 `WinError 5` flake**（实测 7 次中 2 次失败；`test_archive_projection.py` 不在本集合内，说明同一根因在不同集合上的表现），判据见 §1.3：核对错误类型为 `WinError 5` 后按「同一用例重跑 3 次全绿」放行。**期望收集数 = 132**（本集合实测，用于自检集合本身没写错）；其中守卫用例 ID 为 `test_project_context.py::test_dotenv_is_disabled_for_test_runs`，生产调度路径用例为 `test_project_context.py::test_tool_dispatch_uses_context_not_process_cwd`。
- [ ] 5.9 运行 `git diff --check`（期望无输出，exit 0）。**覆盖面声明**：该命令不检查 untracked 文件，本轮新增的 project_context.py / conftest.py / test_project_context.py 不在其覆盖内；如需覆盖，先 `git add --intent-to-add` 再检查，或另行声明未跟踪文件不参与本检查。
- [ ] 5.10 完成 V(C01)：独立冻结差异审查 + 风险对应 GapClosure，记录 contract_revision、accepted_result 与 contract_handoff，逐字记录 `git status --porcelain` 与全量 SHA256，并同步唯一台账。

## 6. 当前工作树状态登记（未经 D(C01) 通过）

以下文件已在本轮落盘，属"实现存在但 D(C01) 尚未接受"，不计入上面的完成度：

| 文件 | 状态 | 对应任务 | 说明 |
| --- | --- | --- | --- |
| `src/mini_claude/project_context.py` | 新增（untracked） | 3.1—3.3 | 模块级可变单例与 cwd 回退已在评审后移除；realpath 为唯一规范化来源 |
| `src/mini_claude/tests/conftest.py` | 新增（untracked） | 2.1、2.4 | 隔离强制点（顶层无条件置 dotenv 开关 + 临时 HOME/RUNTIME_DIR + monkeypatch 导入期常量） |
| `src/mini_claude/tests/test_project_context.py` | 新增（untracked） | 2.2、5.1、5.3、5.5、5.7 及部分 5.2 | 守卫测试（N2 修复载体）、身份独立复算与负向绑定、等价类归一、拒绝输入、`must_exist=False`、memory 冻结与索引、skills/agents/memory 隔离、调度路径（工具/文件/权限）、子 Agent 继承、入口失败契约 |
| `src/mini_claude/skills.py` | 修改 | 4.4 | context 参数 + 按 workspace_id 分键缓存 |
| `src/mini_claude/memory.py` | 修改 | 4.5 | memory 目录改由 `context.resolve_memory_dir()`；数据根随 `runtime_data_dir` |
| `src/mini_claude/subagent.py` | 修改 | 4.4 | 项目 agents 目录改由 context；缓存按 workspace_id 分键 |
| `src/mini_claude/prompt.py` | 修改 | 4.3 | CLAUDE.md 遍历、rules、`{{cwd}}`、git 子进程 `cwd=` 全部改用 context |
| `src/mini_claude/tools.py` | 修改 | 4.1 | `_run_shell` 显式 `cwd=`；文件三工具与 `list_files`/`grep_search` 按 context 解析相对路径；`load_permission_rules`/`check_permission`/`commit_tool_state`/`_auto_update_memory_index` 全部接 context |
| `src/mini_claude/mcp_client.py` | 修改 | 4.2 | `McpManager` 持 context 快照，项目配置从 `context.settings_path` / `context.mcp_config_path` 读取 |
| `src/mini_claude/agent.py` | 修改 | 4.6 | 构造 context 并贯穿提示词/技能/子 Agent/记忆预取/MCP；工具与权限调用点传 `context=self.context`；skill-fork 构造移入 `try` |
| `src/mini_claude/__main__.py` | 修改 | 4.7 | 入口一次性构造 context 并注入 Agent；`_entry_workspace()` 把 cwd 失败转为可诊断错误并以 exit 2 退出 |
| `src/pyproject.toml` | 修改 | 2.3 | `python-dotenv>=1.2.0` |
| `AGENTS.md` | 修改 | 范围外（项目契约初始化） | 用户授权写入的 Harness Project overlay marker |

**已知遗留（如实登记，不计入已完成）**：
1. session 根（`session.SESSION_DIR` 导入期常量）与 artifacts 根（`runtime_data_dir()` 直读环境）**尚未由 context 派生**；本 Change 只统一了 memory 根，R6 的会话/工件侧统一归 C03/C05 的输入面。
2. tasks 5.6 的旧会话只读（workspace 绑定字节级证明）按 spec 与非目标**移交 C03**。
3. flake：`session.py:91` 的 `os.replace` 偶发 `WinError 5`（实测率约 2/7，非固定集合）。根因代码未被本 Change 改动（blob 同 HEAD），但**触发条件是否受本 Change 影响尚未定性**；判据见 §1.3。

已完成并解除登记的遗留（本轮）：`/memory`、`/skills`、`/<skill>` 三条 REPL 命令现已透传 `agent.context`；普通 `agent` 工具分支已有继承用例；`tools._matches_rule` 对文件类规则已在两侧同基准解析（context 根）。

## 7. 跨 Change 接口交接清单（tasks 4.8 产物）

本 Change 改变了以下公开签名与语义，后续 Change 必须以本清单为输入重新确认自身接线；未在 C02—C04 变更准备中重新验证的接线点，不得假定仍然可用。

| 接口 | 变化 | 受影响消费者 |
| --- | --- | --- |
| `ProjectContext` / `from_root()` / `resolve_workspace_root()` / `workspace_id_for()` / `require_context()` | 新增能力；`require_context(None)` 抛 `ProjectContextError` | C02（输出端口构造）、C03（应用层与 owner 锁以 workspace 身份分键）、C05（host 启动按 workspace 构造） |
| `Agent(..., project_context=...)`、`Agent.context` | 新增 keyword-only 参数与实例属性 | C02（TUI 端口注入）、C03（应用层持有 context）、C04（投影按 workspace 过滤） |
| `execute_tool_value(..., context=)` / `execute_tool(..., context=)` / `commit_tool_state(..., context=)` | 新增 keyword-only `context`，缺省时在调用点构造 | C05（host 调度必须传入）、C04（若经工具边界观察） |
| 工具相对路径语义（`read_file`/`write_file`/`edit_file`/`list_files`/`grep_search`/`_run_shell`） | 相对路径以 `context.tool_cwd` 解析；subprocess 显式 `cwd=` | C05、C06 |
| `discover_skills(context=)` / `get_skill_by_name(..., context=)` / `execute_skill(..., context=)` / `build_skill_descriptions(context=)` | 新增可选 context；缓存按 `workspace_id` 分键 | C02（TUI 技能命令）、C04（GUI 技能列表） |
| memory 家族（`get_memory_dir(context=)` 等） | memory 根随 `runtime_data_dir`（有意统一）；`resolve_memory_dir()` 为冻结访问器 | C04、C05 |
| `build_system_prompt(context=)` / `load_claude_md(context=)` / `get_git_context(context=)` | 新增可选 context；rules 从 `context.root` 读取（曾因双重拼接回归，已修复并有用例） | C02、C05 |
| `McpManager(context=)` / `McpConnection(..., cwd=)` | 项目配置从 context 读取；MCP 子进程显式 `cwd=context.tool_cwd` | C05、C06 |
| `check_permission(..., context=)` / `load_permission_rules(context=)` / `_matches_rule(..., context=)` | 项目 settings 与规则路径按 context 解析；缓存按 `workspace_id` 分键 | C03（审批策略）、C06（GUI 审批展示） |
| `main()` 入口 | 一次性构造 context；workspace 解析失败 exit 2（可读消息、无堆栈） | C05/C06（由 worker/GUI 接管的入口形态） |

未由本 Change 派生、留给后续 Change 的数据根：`session.SESSION_DIR`（导入期常量）与 artifacts 根（`runtime_data_dir()` 直读环境）—— 属 C03/C05 的输入面。
