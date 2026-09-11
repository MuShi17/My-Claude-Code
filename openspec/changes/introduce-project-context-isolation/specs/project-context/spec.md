## Purpose

为 runtime 引入显式的项目上下文边界：workspace 根目录被规范化并生成稳定身份，文件、配置、规则、MCP、memory、skills 与工具 cwd 全部从同一不可变 ProjectContext 派生，使同一进程内两个 workspace 的配置、数据与工具执行目录互不串用，且不再依赖 `Path.cwd()` 与导入期常量。

本 spec 共 9 条 requirement、26 个 Scenario。

## ADDED Requirements

### Requirement: Workspace 规范化与稳定身份

系统 MUST 通过单一入口把 workspace 根解析为规范化绝对路径，并基于该规范化结果生成稳定 `workspace_id`。

**身份推导（可判定关系式）**：`workspace_id` MUST 由 `os.path.realpath(root)` 的结果**未经 `os.path.normcase` 折叠**逐字派生（`sha256(str(realpath(root)))[:16]`）。任何大小写折叠都 MUST NOT 参与身份推导——这是既有 memory 目录位置不变（见「身份算法变更不移动既有 workspace 的 memory 根」）的前提。

**同一性判据**：两个路径的 `os.path.realpath` 结果相同即视为同一 workspace。系统 MUST NOT **仅因**某路径形式不同而抛错；根不存在或输入无法归一仍按下一条 requirement 拒绝。实现可以比该判据更强（例如平台 `realpath` 已解析 8.3 短名），但更强行为不作为契约；本 Change 不承诺通过 symlink/junction 到达的路径与其目标共享身份。

#### Scenario: 同一目录的不同书写形式归一

- **WHEN** 以 `<tmp>/proj`、`<tmp>/proj/` 与 `<tmp>/proj/sub/..` 三种形式请求 workspace 身份
- **THEN** 三次得到相同的规范化根与相同 `workspace_id`

#### Scenario: 身份不随进程当前目录变化

- **WHEN** 进程当前目录从 A 切到 B 后再请求同一 workspace 的身份
- **THEN** 返回的规范化根与 `workspace_id` 与切换前完全一致

#### Scenario: 等价类外的形式不因形式本身被拒绝

- **WHEN** 以一个不在声明等价类内但真实存在的绝对根请求 workspace 身份
- **THEN** 系统正常返回其规范化根与身份，不抛出歧义或不存在错误

### Requirement: 不存在或不可归一的根必须拒绝

当根目录不存在，或输入无法归一为唯一绝对根（空字符串、仅空白、含 NUL 字符、驱动器相对路径）时，系统 MUST 拒绝并要求明确错误，MUST NOT 猜测根目录、MUST NOT 静默回退到进程当前目录、MUST NOT 创建目录。当进程当前目录本身不可读取（例如已被删除）时，系统 MUST 以同一类可诊断错误失败，MUST NOT 让底层 `OSError` 冒泡为未处理异常。

`must_exist=False` 是公开旁路：它只为需要在创建前解析身份的调用方保留，MUST NOT 被入口用于跳过本 requirement。

#### Scenario: 不存在的根不被静默创建

- **WHEN** 请求的根目录不存在且未获显式创建指示
- **THEN** 系统返回可诊断错误、不产生 `workspace_id`，并且该路径在调用后仍然不存在

#### Scenario: 空输入被拒绝

- **WHEN** 以空字符串或仅空白请求 workspace 身份
- **THEN** 系统返回歧义错误且不产生 `workspace_id`

#### Scenario: 进程当前目录不可读取时可诊断失败

- **WHEN** 进程当前目录不可读取（如已被删除），且调用方未提供 workspace
- **THEN** 系统抛出 ProjectContext 类错误并说明原因，MUST NOT 抛出裸 `OSError`

### Requirement: 入口失败契约可诊断

workspace 解析失败时，入口 MUST 以可诊断方式失败：返回非零退出码并输出说明原因的明确消息，MUST NOT 打印未处理异常堆栈。

输入面说明：本 Change 不新增 CLI workspace 参数（D7 保持「当前目录即 workspace」），因此本平台的入口失败输入面限于「进程当前目录不可用」；其余入口形态属后续 Change 的输入面扩展，不在本 Change 承诺内。

平台限制（如实登记）：在 Windows 本机**无法真实构造**「进程当前目录不可读」——目录被自身进程占用时删除报 `WinError 32`，`subst` 虚拟盘在 `subst /D` 后 `os.getcwd()` 仍返回原路径。因此本 requirement 与「进程当前目录不可读取时可诊断失败」Scenario 的证据只能来自 OS 边界注入（模拟 `Path.cwd()` 抛错），这属于实现限制而非设计选择；核心隔离保证（两个 workspace 不串用）不依赖任何 mock 证据。

#### Scenario: 入口报告 workspace 解析失败

- **WHEN** 入口在 workspace 解析失败的情况下启动
- **THEN** 以非零退出码结束，输出包含失败原因的可读消息，且不出现未处理异常的堆栈

### Requirement: ProjectContext 为不可变且一次解析

系统 MUST 提供 ProjectContext 值对象，在构造时一次性解析 `root`、`workspace_id`、配置根、`runtime_data_dir`、规则/技能/agents/MCP/memory 来源与工具 cwd；构造完成后这些字段 MUST 不可变，消费方 MUST 通过该对象读取。系统与各模块 MUST NOT 在 **ProjectContext 构造完成后**重新读取进程级可变量（当前工作目录、可变全局配置单例）来决定这些字段，MUST NOT 持有模块级可变上下文（含「当前 workspace」单例）。

缺省兼容路径的豁免条款（可判定）：仅当调用方未提供 `context` 时，消费方 MAY 在**调用点**以当时 `Path.cwd()` 为 `base`，经同一入口 `resolve_workspace_root()` 一次性构造局部 ProjectContext；该对象 MUST NOT 被缓存、MUST NOT 跨调用共享、MUST NOT 写回任何模块级状态。入口、以及后续 worker/GUI 等严格模式 MUST 显式传入 context，缺参由 `require_context()` 抛错。

并发边界：本 Change MUST NOT 被理解为承诺多线程同时写缓存的数据竞争安全（owner 锁属 C03）；但在同一事件循环内以多个 context 交错或并发读取时，各自结果 MUST 只含自身 workspace 的内容。

#### Scenario: 构造后进程当前目录变化不影响已解析上下文

- **WHEN** 在构造 ProjectContext 之后改变进程当前目录
- **THEN** 该对象的 root、workspace_id、config_root、runtime_data_dir、tool_cwd 与 memory_root 全部保持构造时的值

#### Scenario: 数据根在构造时取自显式输入或环境

- **WHEN** 以显式 `runtime_data_dir` 构造 ProjectContext，且同一进程内环境变量指向另一目录
- **THEN** 该对象的 `runtime_data_dir` 等于显式输入，两个分别以不同显式数据根构造的 context 得到不同的 `runtime_data_dir`

#### Scenario: 缺少上下文时显式失败而不回退

- **WHEN** 严格要求上下文的调用方未提供 context
- **THEN** 系统抛出 ProjectContext 错误，MUST NOT 回退到进程当前目录

#### Scenario: 缺省兼容路径不留下跨调用状态

- **WHEN** 在进程当前目录为 A 时以缺省方式读取项目级能力，再切到目录 B 后以缺省方式读取
- **THEN** 两次结果分别只对应 A 与 B 的 workspace，且第二次不含 A 的内容

#### Scenario: 两个 context 交错读取不串用

- **WHEN** 在同一事件循环内以 A、B 两个 context 交错（含并发任务）读取项目级能力
- **THEN** 每次结果只含自身 workspace 的定义，不出现混合或残留

#### Scenario: 构造后根被删除时工具执行可诊断失败

- **WHEN** 构造 context 后其根目录被删除，再以该 context 执行工具
- **THEN** 操作以可诊断错误失败，MUST NOT 回退到进程当前目录或其它 workspace 根，且该对象的 `root`/`tool_cwd` 字段不变

### Requirement: 身份算法变更不移动既有 workspace 的 memory 根

`workspace_id` 算法 MUST 保持向后兼容：对未显式指定数据根的既有 workspace，系统 MUST NOT 因身份算法变更而改变其 memory 目录位置。实现 MUST NOT 引入第二套历史身份回退路径作为常规机制；等价性由身份算法本身保证（本 Change 以 `realpath` 作为唯一规范化来源达成）。

#### Scenario: 默认 CLI 场景身份与历史取值一致

- **WHEN** 在默认 CLI 场景（未指定数据根）解析 workspace 身份
- **THEN** 得到的 `workspace_id` 与历史算法 `sha256(str(Path.cwd()))[:16]` 的取值相同

#### Scenario: memory 目录位于该身份之下

- **WHEN** 构造 ProjectContext 并取得 memory 目录
- **THEN** 该目录等于 `<runtime_data_dir>/projects/<workspace_id>/memory`，且同一 context 多次取得的结果完全相同

#### Scenario: 环境变量属构造期输入而非稳定量（已声明行为）

- **WHEN** 在同一进程内，先以环境变量 A 构造 context，再改变 `MINI_CLAUDE_RUNTIME_DIR` 后以环境变量 B 重新构造同一 workspace 的 context
- **THEN** 两次得到的 `runtime_data_dir` 与 memory 根分别取自各自构造期的环境快照；这是**已声明行为**（环境变量属构造期显式输入），不视为违反本 requirement。同一 context 对象内部仍必须保持其构造期取值不变。

### Requirement: 数据根由上下文统一派生

由本 Change 负责的数据根 MUST 由同一 ProjectContext 派生，MUST NOT 各自独立读取环境变量或 HOME 而形成不同来源。消费方 MUST 通过 `resolve_memory_dir()`（或其冻结字段）取得 memory 目录，MUST NOT 用其它推导另建 memory 目录。

行为声明：本 Change 后 memory 根随 `runtime_data_dir`（含 `MINI_CLAUDE_RUNTIME_DIR`）而非固定 HOME；这是对既有不一致（session 读环境变量、memory 固定 HOME）的**有意统一**，在各工件中如实声明。

**范围排除（显式声明）**：canonical session 数据根（`session.SESSION_DIR` 为导入期常量）与 artifacts 数据根（`runtime_data_dir()` 直读环境）**不在本 Change 的统一范围内**，分别移交 C03（应用层数据根）与 C05（host 启动时传入数据目录）。本 Change MUST NOT 被理解为已统一这三个数据根。

#### Scenario: 显式数据根决定 memory 根

- **WHEN** 以显式 `runtime_data_dir` 构造 context
- **THEN** `runtime_data_dir` 与其下的 memory 目录都位于该数据根之下；session 与 artifacts 数据根按上一条范围排除声明处理，不构成本 Scenario 的断言对象

#### Scenario: 同一 context 的 memory 目录取值恒定

- **WHEN** 在同一 context 上多次取得 memory 目录，其间在其它 projects 子目录下创建目录
- **THEN** 每次得到同一路径，不因文件系统变化而改判

### Requirement: 按 workspace 隔离的读取与缓存

由 workspace 派生的读取结果（项目 skills、项目 agents、项目 settings、`.mcp.json`、memory 目录、规则目录）MUST 按 workspace 隔离；任何模块级缓存 MUST 以 workspace 身份为键。在一个 workspace 中解析出的配置与内容 MUST NOT 泄漏到另一个 workspace。

#### Scenario: A/B workspace 的同名定义分别解析

- **WHEN** workspace A 与 workspace B 各自存在 `.claude/skills/<同名>/SKILL.md`，且 A 与 B 各自存在 `.claude/agents/<同名>.md`
- **THEN** 在 A 的 context 下只解析出 A 的 skills 与 agents 定义，在 B 的 context 下只解析出 B 的定义

#### Scenario: 先 A 后 B 再回 A 均得到各自结果

- **WHEN** 在同一进程中依次以 A、B、A 的 context 读取项目级能力
- **THEN** 三次结果分别对应 A、B、A 的定义，且回到 A 时不含 B 的定义

#### Scenario: 同一 context 重复读取保持缓存语义

- **WHEN** 以同一 context 连续两次读取项目级能力且其间未调用 reset
- **THEN** 两次返回同一结果对象；调用对应 `reset_*_cache()` 后再次读取返回新的结果对象

#### Scenario: context 根变化后不再返回旧缓存

- **WHEN** 以根为 A 的 context 读取后，改用根为 B 且 B 下新增了项目级定义的 context 读取
- **THEN** 结果反映 B 的定义，不回落到 A 的缓存

### Requirement: 工具执行目录显式传递

工具执行 MUST 使用 ProjectContext 的 cwd，MUST NOT 依赖进程当前工作目录；shell 类工具 MUST 把该 cwd 显式传给子进程；文件与搜索工具的默认相对路径 MUST 以 context 根解析；子 Agent 继承同一 workspace 上下文而保留自身的 run/context 身份。

#### Scenario: 相同相对路径在不同 workspace 落到各自根

- **WHEN** 在 workspace A 与 B 的 context 下分别以相同相对路径执行 shell 命令写入文件
- **THEN** 真实子进程执行后，A 与 B 的根下各自出现该文件，且相互不覆盖

#### Scenario: 子 Agent 不跨越父 workspace

- **WHEN** 在 workspace A 的 context 下派生子 Agent
- **THEN** 子 Agent 的工具 cwd 等于 A 的根，且其 run 身份与父 run 身份可区分

### Requirement: 旧会话不猜测归属且不自动认领

当会话记录缺少 workspace 绑定元数据时，系统 MUST NOT 依据当前 workspace 猜测归属、MUST NOT 写入绑定元数据、MUST NOT 因读取而启动运行。C01 MUST NOT 定义或写入 workspace 绑定字段：字段名、schema 版本与迁移由 C03 冻结。读取路径上对显示类缓存（`session.v2.json`）的既有刷新行为 MUST NOT 被视为认领，也不在本 requirement 的"未改写"范围内。

#### Scenario: 列出旧会话时不认领

- **WHEN** 在 workspace A 下多次列出缺少绑定元数据的旧会话
- **THEN** 返回结果稳定、未出现绑定字段、未启动运行，且会话目录内不新增绑定文件

#### Scenario: 加载或恢复旧会话不改变归属与事实

- **WHEN** 在 workspace A 下加载或以 resume 方式读取同一旧会话
- **THEN** 该会话仍未被认领到 A；canonical 运行数据与其派生摘要（如 high-water、message count）不变；只有显示缓存的字节与时间戳可能变化
