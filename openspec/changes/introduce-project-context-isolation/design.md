## Context

> 依赖点行号为**编写时快照**，实现推进后已发生漂移；核实依赖请以符号名（函数/常量名）为准，不要直接引用行号。

当前 runtime 用进程全局状态表达「项目位置」，实测到的依赖点包括：

- `tools.py:672`（项目 settings）、`mcp_client.py:251,255`（项目 settings 与 `.mcp.json`）、`subagent.py:90`（项目 agents）、`prompt.py:171`（CLAUDE.md 向上遍历）、`prompt.py:186`（`.claude/rules`）、`prompt.py:229`（`{{cwd}}`）、`skills.py:49`（项目 skills）、`memory.py:46`（memory 目录哈希）均直接调用 `Path.cwd()`。
- `prompt.get_git_context()`（`prompt.py:193-207`）的 `git` 子进程未传 `cwd=`，继承进程当前目录。
- `tools.grep_search` 的 `path="."`（`tools.py:455,520`）与 `_run_shell`（`tools.py:538-544`）同样依赖进程 cwd（`_run_shell` 不传 `cwd=`）。
- 模块级缓存无 workspace 键：`skills.py:30 _cached_skills`、`subagent.py:78 _cached_custom_agents`、`tools.py:655 _cached_rules`。
- `session.py:24` 的 `SESSION_DIR` 是导入期常量，`runtime_data_dir()` 只读 `MINI_CLAUDE_RUNTIME_DIR` 或 HOME。
- 测试侧原本无任何 `conftest.py`；`PYTHON_DOTENV_DISABLED` 由 python-dotenv 1.2.0 起支持（实测 1.2.2 下 `load_dotenv()` 返回 `False` 且不注入仓库根 `.env` 中的真实密钥），但它在**导入期**判定，`monkeypatch.setenv` 在测试体内设置已太晚。

约束：CLI/TUI 与 Harbor 入口的既有 flags 与默认路径语义必须保持；不得移动历史数据目录；本 Change 不引入 owner 锁与 Application API（属 C03）。本设计同时承担 Item02 独立评审结论中 GAP-I02-01 的闭合。

术语消歧：本 Change 的 "context" 指**workspace/项目上下文**，与既有 change 中的 model-context 身份（`canonical-model-context`、`effective-context-transition`、`isolate-agent-context-identity`）无关，不改变后者语义。

## Goals / Non-Goals

- Goals：workspace 身份稳定且可拒绝歧义输入；同一进程内两个 workspace 的配置、数据与工具 cwd 互不串用；runtime 的**文件/配置/规则/技能/agents/MCP/memory/工具 cwd** 解析不再依赖 `Path.cwd()`（`session.SESSION_DIR` 等导入期常量的会话侧数据根按 spec R6 的范围排除声明移交 C03/C05，见 Open Questions）；测试具备全局强制隔离点。
- Non-Goals：Application API、会话/run 拥有权与取消、跨进程协议、GUI 桌面壳、worker 管理（属 C03/C06）；不改动 canonical 事件与持久化 schema 语义；不冻结会话 workspace 绑定字段与版本（属 C03）。

## Decisions

### D1 用显式参数传递 ProjectContext（备选：继续用进程 cwd + 加锁；备选：进程级当前上下文单例）

消费方（`discover_skills`、`load_permission_rules`、`build_system_prompt`、`_discover_custom_agents`、`load_claude_md`、`_run_shell`、`memory.get_memory_dir`）改为接收显式 `context` 关键字参数。备选一「保留 `Path.cwd()` 并在入口 `chdir` 加锁」被否决：同一进程内的并发消费方无法安全共享可变进程 cwd，且与批次「runtime 不自行切全局 cwd」约定冲突。备选二「进程级当前上下文单例（`set_current_project_context()`）」同样**被否决**：它在进程内复活「单个当前 workspace」，只剩「最后一次 set 生效」，与 C01「同一进程内两个 workspace 互不串用」结构性矛盾，且引入了策略难以覆盖的可变全局状态。**本 Change 明确不提供进程级可变当前上下文。**

兼容策略（可判定，避免破坏既有调用方）：`context` 允许为 `None`，此时消费方**在调用点一次性**由 `Path.cwd()` 构造局部 ProjectContext（等同 CLI 默认「当前目录即 workspace」），不缓存、不跨调用共享、不写回模块级状态。因此：

- 既有 `discover_skills()` 无参调用与 `test_skills.py` 中 17 处 `patch(Path.cwd)` 保持可用；
- 模块内不再持有任何进程级上下文状态（模块导入期不读环境、不留全局单例）；
- 需要严格模式（入口、未来的 worker/GUI）的调用方传显式 context，缺参时由 `project_context.require_context()` 抛 `ProjectContextError`。
- 该豁免只覆盖「未提供 context 时的一次性构造」；ProjectContext 构造完成后不得再读 cwd 决定其字段。并发边界：本 Change 不承诺多线程同时写缓存的数据竞争安全（owner 锁属 C03），但同一事件循环内多 context 交错读取 MUST 不串用。

### D2 规范化在单一入口完成，歧义即拒绝（备选：宽松回退到 cwd）

`resolve_workspace_root()` 使用 `realpath` 解析别名与链接、要求根目录存在。无法唯一确定时抛出明确错误。备选是缺省回退到 `Path.cwd()`：被否决，因为 GUI 会让用户从任意位置启动，静默回退会把「选错目录」变成「写错项目」。

等价类边界（可判定，避免与 spec 的 MUST 范围矛盾）：本 Change 保证的等价类是「同一 `realpath` 结果相同」——**大小写折叠 MUST NOT 参与身份推导**（见 D3 与 spec R1）。UNC 路径与映射盘、8.3 短名**不在**保证范围内：若两者归一到不同字符串，系统按不同 workspace 处理，并在文档中声明该限制；无法归一的输入（空串、含 NUL、驱动器相对路径 `C:foo`）直接拒绝。

### D3 身份算法以 realpath 为唯一规范化来源（备选：normcase 折叠 + 历史哈希回退）

`workspace_id = sha256(str(realpath(root)))[:16]`。本机 Python 3.14.6 实测：`os.path.realpath` 已把大小写等价形式、尾分隔符、`..` 段与 8.3 短名解析为真实长名，因此 `D:\workspace\My-Claude-Code`、`d:/workspace/My-Claude-Code/`、`D:\workspace\My-Claude-Code\src\..`、`D:\WORKSPACE\MY-CLAUDE-CODE` 全部得到 `D:\workspace\My-Claude-Code`，其身份 `514fed37912aa299` **正是历史算法 `sha256(str(Path.cwd()))[:16]` 的取值** → 既有 memory 目录无缝、无需任何回退分支。

早期草案曾用 `normcase(realpath)` 做身份并配 `legacy_workspace_id` 回退，评审发现该回退在实现层恒不生效（`resolve_workspace_root()` 返回的已是折叠后路径，legacy 哈希被同一路径喂入），且 `MINI_CLAUDE_RUNTIME_DIR` 场景下回退基址与历史实现（`Path.home()/".mini-claude"`）不一致。已整体删除该机制：**规范化的唯一来源是 realpath，身份推导 MUST NOT 做 normcase 折叠**（见 spec R1 的可判定关系式）。同一性判据也改为 realpath-only；`normcase` 不再出现在判据或身份推导中。

memory 路径在 `ProjectContext` 构造时**冻结**为字段，`resolve_memory_dir()` 退化为纯访问器：不会因文件系统变化改判，从而结构上不可能出现「同一 workspace 两个 memory 根」。

### D4 workspace 派生的缓存按身份分键（备选：进程启动时清缓存）

`skills`/`agents`/`rules`/`settings`/`mcp` 的解析结果以 `workspace_id` 为键缓存，并保留既有 `reset_*_cache()` 语义。同一 workspace 重复调用必须返回**同一对象**（既有 `test_skills.py:496` 的 `s1 is s2` 契约），切换 workspace 不残留，切回原 workspace 仍得到原结果。备选「退出时清空全局缓存」被否决：进程内可能有多个 workspace 同时活跃，清空会互相干扰。

### D5 会话只读不认领，绑定字段留给 C03（备选：本 Change 追加绑定字段）

C01 只承诺三件事：不依据当前 workspace 猜测归属、不写入绑定元数据、不改写既有会话数据。会话元数据由 canonical projection 生成（`session.py:120-130`），且 `load_session()` 在读路径上会重写 `session.v2.json`（该项目自述为 disposable display cache），而 canonical schema 变更属本设计 Non-Goals。因此 workspace 绑定字段名、schema 版本、迁移与回退限制**整体留给 C03**（与任务卡 §2 一致），本 Change 不承诺任何写侧行为，也不宣称"读取必然零写入"。

### D6 测试隔离用 conftest 强制点 + 上游 dotenv 开关（备选：新增自定义开关）

新增 `src/mini_claude/tests/conftest.py` 作为唯一强制隔离点：

- **模块顶层**（任何 `mini_claude` 导入之前）无条件赋值 `os.environ["PYTHON_DOTENV_DISABLED"] = "1"`（不用 `setdefault`：外部若给 `0`/`false` 会静默关闭隔离）；因为 `mini_claude.__main__` 在导入期调用 `load_dotenv()`，fixture 内设置已太晚。开关沿用 python-dotenv 上游名字，**不新增产品侧配置**；`src/pyproject.toml` 的 `python-dotenv` 下限由 `>=1.0.1` 提到 `>=1.2.0`（该开关自 1.2.0 引入）。
- 每个测试获得临时 HOME / USERPROFILE / `MINI_CLAUDE_RUNTIME_DIR`；显式 monkeypatch 导入期常量（`session.SESSION_DIR`），因为导入后改环境变量不会重定向已固化的常量。
- 守卫测试断言 `load_dotenv() is False` 且进程内无 `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`：在旧版 dotenv 下显式红灯，而不是静默读真实密钥。
- 数据根统一：测试内所有数据根由同一组环境变量与常量替换决定；`runtime_data_dir()` 为**函数**，需要时按调用期语义 patch，而不是只 patch 常量。

### D7 保留 CLI 默认语义（备选：强制显式传入 workspace）

CLI 未显式指定 workspace 时，默认使用进程当前目录作为根，仅在构造 ProjectContext 的那一次读取 cwd，之后一律走 context。备选是要求所有入口显式传入：被否决，会破坏既有 flags 与 Harbor 入口兼容性。

### D8 入口失败契约（新增，对应评审 M5）

`WorkspaceNotFoundError` 与 `AmbiguousWorkspaceError` 由入口捕获并转换为**可诊断的 CLI 错误**（非零退出码 + 明确消息，不打印堆栈），以便 GUI 与后续 worker 能把「选错/不可用目录」呈现为可操作反馈。本 Change 只落实 CLI 入口的最小行为；GUI 侧展示属 C06。

## Risks / Trade-offs

- [Windows 路径别名与短名（8.3）、UNC 与映射盘可能不归一到同一身份] → 等价类在 D2 显式限定为「同一 `realpath` 结果」，并在测试中用真实临时目录固定覆盖大小写、尾分隔符与 `..` 段（平台 `realpath` 的实际覆盖**强于**契约，但不作为契约）；超出等价类的输入按不同 workspace 处理并在文档声明。
- [memory 目录身份变化导致既有用户数据静默失联] → D3 以 `realpath` 作为唯一规范化来源，使新旧身份取值一致（实测本仓库 `514fed37912aa299`）；**不再需要**任何历史哈希回退机制，spec 以「身份算法变更不移动既有 workspace 的 memory 根」作为不变量固定该行为。超出 D2 等价类的别名形式（UNC、映射盘）按不同 workspace 处理并已声明。
- [模块级缓存在改造中遗漏，导致 workspace 之间串用] → 用「先 A 后 B 再回 A」的场景测试固定；缓存必须显式以 `workspace_id` 为键；同时保留 `reset_*_cache()` 语义与 `s1 is s2` 对象同一性。
- [旧会话 unbound 处理可能被误当成自动认领，或把读路径的既有重写当成回归] → 只读入口不写绑定元数据；测试断言"未认领、未启动 run、既有数据未被改写"，并对 list 与 load/resume 两条路径分别给出预期。
- [测试隔离改造影响既有回归] → 先建立 conftest 强制点并重跑全量回归，再逐个改造依赖 cwd 的测试；回归基准在每个实施轮次、且在该轮写入停止后重新记录，不沿用旧数字。
- [依赖下限变更（dotenv >=1.2.0）] → 属如实声明的依赖变化；若环境无法升级，守卫测试会显式红灯而非静默失效。

## Migration Plan

1. 冻结基线：记录 runtimePython 绝对路径与版本、HEAD、工作树逐字 `git status --porcelain` 与新增文件 SHA256。**基线必须在该轮写入停止后重冻**；历史数字（如 358 / 374）一律作废，不得沿用。
2. 建立 `tests/conftest.py` 隔离点并验证既有回归仍全绿；隔离守卫测试必须放在**被收集的测试模块**（`tests/test_project_context.py`），因为 conftest.py 中的 test 函数不会被 pytest 收集。
3. 新增 `project_context.py`，随后按模块逐个接入显式 context，每一步保持 CLI 可用并跑局部回归。
4. memory：目录在 context 构造时冻结；数据根统一由 context 派生（memory 随 `runtime_data_dir`，这是对既有 session/memory 不一致的有意统一）。
5. 回滚策略：改动集中在新增文件与接线点，回退即恢复原模块；不涉及数据库 schema 变更与数据移动。

## Open Questions

- 会话 workspace 绑定的字段名、schema 版本与回退限制（归 C03 冻结，本 Change 不实现）。
- `workspace_id` 的 16 hex 长度在极端多 workspace 场景是否需加长；当前按 C01 范围保留。
- 超出 D2 等价类的 UNC/映射盘/8.3 短名场景是否需要专门归一（需要真实环境证据，本 Change 不承诺）。
