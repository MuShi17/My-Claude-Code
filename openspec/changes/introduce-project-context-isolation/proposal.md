## Why

当前 runtime 通过进程全局状态解析项目位置：`Path.cwd()`、按 HOME 派生的固定目录、导入期常量（如 `session.SESSION_DIR`）以及模块级配置读取。这使同一进程内无法安全服务两个不同 workspace，也无法让本机桌面 GUI 通过 sidecar worker 固定服务某个项目根。GUI 批次（C01 起）要求「一个 workspace 一份隔离的配置、数据与工具 cwd」，因此必须先把隐式全局依赖替换为显式传递的 ProjectContext。

术语消歧：本 Change 的 "context" 指 **workspace/项目上下文**，不改变既有 model-context 身份语义（`canonical-model-context`、`effective-context-transition`、`isolate-agent-context-identity`）。

## What Changes

- 新增 `src/rollo/project_context.py`：规范化 workspace 根目录，生成稳定 `workspace_id`，并固定 `root`、配置根、`runtime_data_dir`、规则/技能/agents/MCP/memory 来源与工具 cwd。
- 新增不可变 ProjectContext 值对象：构造时一次性解析上述来源，之后不再读取进程级可变量；**不提供**进程级可变「当前 workspace」（D1）。
- 改造 runtime 消费方：`agent.py`、`prompt.py`、`tools.py`（含经 `execute_tool_value` 的调度路径）、`memory.py`、`skills.py`、`subagent.py`、`mcp_client.py`、`__main__.py` 入口接收显式 context 并向下传递；子 Agent 继承父 workspace 上下文。`session.py` 的 workspace 绑定字段不在本 Change（属 C03），其存储路径与语义保持不变。
- 工具显式使用 context 的 cwd：shell 传递 `cwd=`，文件/搜索工具的默认相对路径以 context 根解析。
- 路径规范化覆盖 Windows 大小写、尾分隔符与 `..` 段；无法可靠归一（空串、含 NUL、驱动器相对路径）时**拒绝**，而不是猜测；本 Change 的等价类边界与超出范围的别名形式在 design D2 显式声明。
- **兼容**：身份算法以 `realpath` 为唯一规范化来源，与历史算法 `sha256(str(Path.cwd()))[:16]` 取值一致（本机实测 `514fed37912aa299`），既有 `~/.rollo/projects/<hash>/memory/**` 不搬移、不复制、不失联（D3）。
- 旧会话保持立场为「不猜测归属、不写绑定、不改写数据」；workspace 绑定字段与版本**留给 C03**（D5）。
- 不移动历史数据目录、不改变 `ROLLO_RUNTIME_DIR` 语义、不改变 CLI 默认「当前目录即 workspace」的对外行为。
- 入口新增最小失败契约：workspace 不可用/歧义时给出可诊断错误与非零退出码，不打印堆栈（D8）。
- 依赖变化：`python-dotenv` 下限由 `>=1.0.1` 提到 `>=1.2.0`（测试隔离使用其上游 `PYTHON_DOTENV_DISABLED` 开关，该开关自 1.2.0 引入）。

## Capabilities

### New Capabilities

- `project-context`: workspace 规范化身份与等价类边界、显式 ProjectContext 解析与不可变性、按 workspace 隔离的读取与缓存、工具 cwd 显式传递、memory 目录的历史兼容、以及入口失败契约。

### Modified Capabilities

（无。`openspec/specs/` 当前只有 `.gitkeep`，没有已归档 main spec；本 Change 不依赖也不修改任何既有 capability 的 requirement。）

## Impact

- 新增：`src/rollo/project_context.py`、`src/rollo/tests/test_project_context.py`、`src/rollo/tests/conftest.py`（测试隔离强制点，闭合 GAP-I02-01）。
- 修改：`agent.py`、`prompt.py`、`tools.py`、`memory.py`、`skills.py`、`subagent.py`、`mcp_client.py`、`__main__.py` 的上下文接线；`src/pyproject.toml` 的依赖下限。
- 兼容性：CLI/TUI 与 Harbor 入口保持既有 flags 与默认路径语义；数据目录不做破坏性迁移；既有 memory 目录位置不变（身份取值一致）。
- 非目标归属：Application API 与 owner 锁属 C03；stdio host 属 C05；worker 管理属 C06。
- 跨 Change 影响面：本 Change 触及 8 个消费方模块，后续 C02（输出端口）、C03（应用层与取消）、C04（投影订阅）需以本 Change 的 context 契约为输入重新确认自身接线；具体重新验证集合在各 Change 的变更准备中列出。
