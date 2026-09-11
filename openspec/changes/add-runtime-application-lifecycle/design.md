## Context

C02 已把 runtime 产生的输出和人工交互抽象为 `OutputPort`、`InteractionPort`，并由 `InteractionRegistry` 负责交互请求的身份校验、单向状态转移和回复幂等。`RuntimeEventEmitter`、`DurableToolBoundary` 和 `SQLiteRuntimeStore` 已经分别承担 canonical 事件、工具操作耐久边界和事件提交；SQLite 写入使用受保护事务，并在提交后才返回成功。

当前入口仍由 `__main__.py` 直接构造和持有 `Agent`。REPL 的 SIGINT 路径读取 `Agent._aborted`/`_output_buffer`，运行取消、会话恢复、终端输入和受管理 shell 还没有一个可供 TUI、未来 GUI 及其他进程内调用方复用的统一控制面。C03 要解决的是应用层生命周期和拥有权，而不是再次实现事件事实源或新增跨进程 wire 协议。

本设计对应 proposal 中的四个 capability：`runtime-application-api`、`runtime-execution-ownership`、`runtime-control-persistence` 和 `tui-application-integration`。它也遵循四份 delta spec：公开 API 只返回结构化控制结果，所有副作用命令先完成幂等记录和提交，再允许 dispatch；恢复不得猜测或自动重放不确定工具；TUI 继续消费 C02 端口。

约束如下：

- `ProjectContext` 是所有严格入口的唯一 workspace 来源，规范化后的 `workspace_id` 是锁、控制记录和运行身份的绑定键；不得通过当前目录、终端文本或 UI 布尔值补推上下文。
- C02 canonical 事件事实源保持不变。控制记录是控制面状态和恢复证据，不替代事件 ledger，也不把派生 session JSON 当作写入事实源。
- 不新增第三方依赖，不实现 stdio host、GUI 投影、Electron 壳、Windows 安装包或付费 Harbor benchmark。
- C03 实现只能在本 Change 的 design/specs/tasks 通过独立审查并获得主 Agent D(C03) 接受、工作树和 writer 明确后进行；本设计本身不授权代码改动。

## Goals / Non-Goals

**Goals:**

- 建立进程内 `Application` 控制外观，统一承载 `session.create/list`、`run.start/status/cancel`、`interaction.respond` 和 `shutdown`。
- 让命令身份、参数摘要、workspace root owner、child owner 树和生命周期终态具有可恢复、可审计的控制记录。
- 将取消传播到模型流、C02 交互等待、受管理 shell 和 child Agent，并把“任务已请求取消”与“OS 子进程已退出”分开表达。
- 在同一规范化 workspace 上为 TUI 和 Application API 提供同一把 OS 级拥有权锁，防止第二个 root run 静默 dispatch。
- 使 one-shot、REPL、resume 和 TUI 适配器经由 Application API 工作，同时保留既有 flags、权限、退出码、REPL 命令、EOF 语义和 C02 结构化事件边界。
- 以临时 workspace/runtime 目录、离线 provider/worker、真实本地 Python 子进程和 CLI/TUI consumer 组合出分层验证证据，并显式区分替身与真实 OS 进程证据。

**Non-Goals:**

- 不重写 `RuntimeEvent`、`RuntimeEventEmitter`、session projection 或 C02 port 的 canonical 语义。
- 不把 Application API 做成网络协议；本 Change 只定义进程内调用边界，不创建 stdio/HTTP/RPC host。
- 不在 C03 中交付 GUI、Electron、Windows 打包、跨进程 GUI 通信、部署或真实付费 Harbor 任务。
- 不自动接管、恢复或重放一个没有充分 dispatch/结果证据的旧运行；不通过清空数据库、覆盖旧记录或删除事实解决迁移问题。
- 不以本 Change 的 OpenSpec 工件完成推导 C03 产品代码、Git 提交、推送、MR、发布或部署授权。

## Decisions

### D1：以 Application 作为唯一进程内控制面，Agent 作为受管理执行器

新增 `application.py`，对外提供结构化 command/result 类型和异步控制方法；外部调用方只提交操作名、稳定身份、`ProjectContext`、受限参数及超时，返回 session/run/interaction/owner 状态。Application 内部负责命令校验、幂等查询、拥有权仲裁、状态持久化和执行监督，再把实际模型调用委托给已有 `Agent`。

`Agent` 继续负责 provider loop、工具选择和 C02 事件发布，但不再成为 TUI 的生命周期协议。Application 创建 Agent 时显式注入 `ProjectContext`、`OutputPort`、`InteractionPort`、runtime store 和 owner context；取消通过 Application 的 supervisor 调用公开的 Agent/port 取消能力，并将结果写回控制面。入口不得读取或修改 `_aborted`、`_output_buffer`。

命令名称保持 proposal/spec 中的点号形式，但 Python 方法名和 dataclass 的具体命名属于实现细节；它们必须共同映射到同一份 command envelope，不得为 CLI、TUI、未来 GUI 各自再造一套控制状态机。

**考虑过的替代方案：**

- 让 `Agent` 自身直接暴露全部 session/run API：会把 provider loop、控制面和入口生命周期耦合在一起，且不能自然覆盖跨入口 workspace 锁，因此不采用。
- 让 TUI 继续调用 `Agent.abort()` 并通过私有字段判断运行中：无法支持外部状态查询和重启恢复，也会把终端适配器重新变成控制协议，因此不采用。
- 新建 HTTP/stdio host 作为统一入口：超出 C03 的进程内边界，并会把跨进程协议问题提前混入本 Change，因此不采用。

### D2：用命令 envelope 和单一状态串行器实现幂等与终态唯一

所有 `run.start`、`run.cancel`、`interaction.respond` 等有副作用操作使用统一 envelope，至少包含 `command_id`、`scope_type`、`scope_id`、`operation`、`params_digest`、`session_id`、可选 `run_id/request_id`、提交时间和 schema 版本。参数摘要只覆盖允许持久化的规范化参数，不保存 API key、完整 provider 配置或原始敏感载荷。

Application 先在同一个控制边界内按 `(scope_type, scope_id, command_id)` 查询：相同摘要返回第一次的响应/目标身份；摘要不同返回冲突；未知命令再进入状态转移。进程内用一个按 workspace 的异步串行器避免竞态，跨进程依靠 SQLite 受保护事务和 workspace owner 锁共同约束。终态转移使用集中 guard，成功、失败、取消、interrupted、uncertain 只能单向提交一次；取消与终态竞争时，谁先在控制事务中成功落定谁拥有终态，另一方只能读取已落定结果。

**考虑过的替代方案：**

- 只在内存中用 `asyncio.Lock`：只能保护一个 Application 实例，不能阻挡第二个 TUI/进程，因此不采用。
- 只依赖事件序号推导 command 幂等：canonical 事件可保持事实，但无法表达响应丢失后的控制重试和摘要冲突，因此单独保存控制命令记录。
- 让 cancel 无条件覆盖成功/失败：会制造两个终态或伪造取消成功，违反生命周期 spec，因此不采用。

### D3：控制记录使用 workspace 控制库，canonical 事件库保持事实边界

新增版本化的 workspace 控制存储层，默认位于 `ProjectContext.runtime_data_dir` 下按 `workspace_id` 隔离的应用控制目录；具体文件名和目录迁移必须在任务中与现有 `runtime_store_path()` 兼容性核对后确定。控制层保存 session index、run control、command、owner、pending-interaction 和迁移诊断，canonical 事件仍写入 C02 现有 `SQLiteRuntimeStore`。

一次副作用命令遵循固定顺序：

1. 规范化上下文、身份和受限摘要，检查 owner/状态/交互请求。
2. 在 `BEGIN IMMEDIATE` 或等价受保护事务中写入 command accepted、必要的 run/interaction 状态和 dispatch intent。
3. flush/commit 成功后才调用 Agent、InteractionRegistry 或受管理 shell。
4. 将 dispatch 已观察到的阶段、返回值或错误再次写入控制记录，并由 C02 canonical 事件记录实际运行事实。

提交前进程崩溃不能留下无控制记录的 dispatch；提交后至 dispatch/结果记录之间的崩溃被恢复为 `interrupted` 或 `uncertain`，不能靠自动重试弥补 exactly-once 的未知窗口。响应丢失时，原 command 记录是重试的唯一命中源，不重复调用 provider/tool。

**考虑过的替代方案：**

- 把控制字段直接塞进 canonical event payload：会改变 C02 事实 schema，并把控制重试和业务事实混为一层，因此不采用。
- 继续把 session JSON 当主数据库：当前 JSON 是 canonical projection/cache，不能提供跨入口原子命令和 owner 互斥，因此不采用。
- 通过重建/删除旧库完成迁移：会破坏历史事实且无法恢复未知结果，因此不采用。

### D4：workspace 锁使用 OS 持有句柄，owner 元数据只做诊断

实现 `workspace_lock.py` 的后端接口，以规范化 `workspace_id` 计算锁键，并使用标准库实现的 OS 级独占句柄/咨询锁。锁的“是否持有”由活跃句柄决定，而不是 lock 文件是否存在或其中记录的 PID；锁记录中的 `owner_id`、root/run/session、创建时间和版本仅用于冲突诊断。TUI 和 Application 都从同一个工厂取得同一个锁键。

root owner 成功后生成不可变 `owner_id`，写入控制记录并把 root、child Agent、managed shell 和派生句柄挂到同一 owner tree。只有持有相同 root owner 的控制边界才能创建/取消/释放 child；锁释放必须校验 token，不能因为另一个调用方失败而释放他人的锁。不同 workspace 使用独立键和控制目录。

跨平台细节由后端隐藏：Windows 使用可证明的独占文件句柄/平台锁语义，其他平台使用相应 OS advisory lock；不得把 PID 文件存在性作为互斥实现。真实多进程测试必须覆盖“入口 A 持锁、入口 B 争用、A 退出后 B 才能取得”的顺序，并验证仍存活子进程时不提前释放锁。

**考虑过的替代方案：**

- 只在 `Application` 上放布尔值：无法覆盖第二个进程。
- 只创建 `.pid` 文件：崩溃后容易陈旧，且文件存在不等于持锁。
- 让每个 child 自己申请 root：会绕过 workspace 互斥和父子取消树，因此不采用。

### D5：以 ManagedExecutionHandle 统一取消与 OS 退出观察

模型任务、交互等待、child Agent 和 shell 都注册到 root owner 的 supervisor。每个受管理项有稳定 execution identity、parent/root owner、requested 状态、confirmed 状态和未完成原因。`run.cancel` 只接受一次有效取消命令，然后按模型/交互/child/shell 顺序传播；后续相同命令返回同一结果，错误身份不得触碰执行树。

受管理 shell 由标准库 subprocess 封装为异步 handle，启动时绑定 workspace cwd 和 owner；stdout/stderr 由独立 drain 任务持续读取，取消先执行有界优雅停止，再按策略强制终止，最后等待并记录真实 return code/仍运行状态。取消 asyncio task 或 provider task 只表示任务控制状态变化，不能直接推导 OS 子进程死亡。若超时仍存活，run 保持 `cancelling`/`interrupted`/`uncertain` 等未完成状态并保留 owner 锁。

shutdown 使用明确的阶段状态：禁止新的 start → 请求并等待活跃执行 → flush partial/terminal 控制记录和 C02 事件 → 关闭 MCP/store 等资源 → 仅在确认完成后释放 owner。任何阶段超时返回未完成项和当前 OS/owner 证据，下一次启动可读取，不伪造成功关闭。

**考虑过的替代方案：**

- 取消时仅调用 `asyncio.Task.cancel()`：不能终止 shell，也无法确认 stdout/stderr drain。
- 直接 `kill` 后立即释放锁：可能遗留子进程和写入，违反“仍运行不释放”的边界。
- 让每个 tool 自己实现取消：传播顺序和幂等无法统一，因此由 owner supervisor 统一编排、由各 handle 报告真实状态。

### D6：TUI 是 Application 的消费者，C02 port 仍是事件/交互端口

`__main__.py` 和 `tui_adapter.py` 只负责解析既有 flags、创建 `ProjectContext`、构造终端 ports、显示事件和读取输入；one-shot、REPL、resume、SIGINT、EOF 以及 REPL 命令转换为 Application command。SIGINT 的首次运行中取消通过公开 `run.cancel`，第二次退出走 `shutdown`/CLI 既有退出路径，不读取 Agent 私有状态。

TerminalOutputPort 继续复用 `ui.py`，不解析控制台文本来获得状态；TerminalInteractionPort 继续是终端输入的唯一位置，并把 `InteractionRequest`/`InteractionReply` 交给 C02 `InteractionRegistry` 的同一身份校验。plan approval 若需要额外输入，也必须通过 Application 绑定的交互桥接，不能成为隐藏的第二控制协议。CLI flags、permission mode、退出码和拒绝/EOF 语义用现有行为回归测试锁定。

**考虑过的替代方案：**

- 在 TUI 中复制一份 session/run 状态机：会让两个入口的 owner、cancel 和恢复语义漂移，因此不采用。
- 让 TUI 直接订阅 SQLite 或终端日志：SQLite/event projection 不是控制命令边界，终端日志也不具备身份和幂等语义，因此不采用。

### D7：恢复按证据分类，默认 fail closed

启动 Application 时先读取控制记录、C02 canonical ledger 和已知 migration version，再生成恢复诊断：已接受但未确认 dispatch 的命令/运行标为 `interrupted` 或可安全处理的未执行状态；已观察到工具请求但结果提交不完整的标为 `uncertain`。Unknown tool name 是执行前的查询失败，unknown/uncertain tool result 是执行后证据不足，两者分别记录、分别测试。

恢复只提供状态查询、显式 resume/人工决策和安全的只读 projection；禁止自动重放可能有副作用的工具。旧 session 先以只读形式解析，迁移写入新版本前保留原记录和未知字段；迁移失败只返回诊断，不删除、覆盖或静默绑定到当前 workspace。没有可证明 workspace 归属的旧记录不得被当前 `ProjectContext` 自动认领。

## Risks / Trade-offs

- **[Risk] commit 后 dispatch 前存在 crash window，无法证明 exactly-once。** → 记录 accepted/dispatch intent 的阶段，重启显式标为 interrupted/uncertain，禁止自动重放；把“可安全查询”和“需人工决策”分开验收。
- **[Risk] Windows 进程树终止语义可能因 shell 包装器不同而变化。** → 把 OS handle 做成平台后端，使用真实本地 Python 子进程覆盖优雅停止、强制终止、stdout/stderr drain 和超时保锁；未覆盖的 shell 行为不宣称 C03 通过。
- **[Risk] 新 workspace 控制目录与既有全局 session 目录并存时可能出现列表/恢复分裂。** → `session.py` 增加明确的 workspace 绑定/兼容适配，canonical store 仍是唯一事实；对无绑定旧 session 返回可诊断状态，不按当前目录猜测归属。
- **[Risk] Application supervisor 与 Agent 现有取消入口重复传播。** → 所有传播动作绑定 command_id/execution identity，并由一个 owner supervisor 去重；对重复 cancel 做相同结果回读测试。
- **[Risk] TUI 兼容层迁移期间可能改变退出码或 plan/EOF 行为。** → 在接线前冻结现有 CLI/TUI 回归样本，以相同 flags 和离线替身做 consumer 验证；任何必要变化先更新 spec，不以适配器临时分支绕过。
- **[Risk] 控制记录保存过多参数导致秘密泄露。** → 只保存 schema 白名单字段、类型化摘要和 digest；对 API key、provider 配置、原始交互输入做负向断言，并对日志/异常同样检查。
- **[Risk] 复用现有 SQLite store 时把控制事务误当成 canonical event 事实。** → 明确两层表/接口和提交责任，控制层只记录控制证据，canonical 事件仍通过现有 store/事件 emitter 写入；评审时逐项核对调用链。

## Migration Plan

1. 在实现前先锁定 C02 handoff（`tasks §6.2`）和现有 store/session/port 的事实，补齐 Application API、owner、migration 和 TUI consumer 的测试夹具边界。
2. 先添加版本化控制 schema 与只读加载/诊断，再添加写入事务和 workspace lock；老 session 只读兼容，明确迁移版本后才生成新控制记录。
3. 接入 command idempotency、run state guard、owner supervisor 和 managed shell，再把 `Agent` 的运行和取消回调纳入 Application；每一步先跑 focused tests，保持 C02 canonical tests 作为回归层。
4. 最后替换 one-shot、REPL、resume 和 TUI 的入口 wiring，运行离线 consumer、同 workspace 多进程争用、交互等待取消、真实子进程超时和恢复不重放测试。
5. 只有独立设计审查、独立测试策略、冻结 diff 复核和主 Agent D(C03) 接受都通过，才能进入实现；即使实现通过，也必须另行获得 Git 提交/推送、MR、发布或部署授权。

回滚策略是停止新的 Application wiring，保留 canonical event store 和旧 session 数据，使用兼容读取路径恢复旧入口；不得删除控制记录或覆盖旧 canonical 事实。若迁移失败，保留原版本并让 Application 返回 migration error，待修复后由显式迁移步骤重试。

## Open Questions

- 现有 `SQLiteRuntimeStore` 的 session 级数据库与 workspace 级控制库最终采用独立文件还是同库独立表，需要在实现前通过 schema/锁竞争实验确认；无论选择哪一种，都不能改变 canonical event 的提交与恢复语义。
- Windows 平台的独占句柄后端和受管理 shell 的优雅停止/进程树策略需要以最小真实 Python 子进程实验确定；实验结果必须进入测试策略和验收证据，而不是凭 API 名称推断。
- Application 的公开 Python 方法/返回 dataclass 名称需要在实现任务中一次性冻结，并与点号命令 envelope、C02 `InteractionRequest`/`OutputEvent` 身份字段逐项对应；此问题不允许由 TUI 单独决定。
- 旧 session 缺少 workspace 绑定时，哪些只读 resume 信息可显示、哪些操作必须拒绝，需要由 migration 测试明确；默认不得按当前工作目录自动认领。
- shutdown/cancel 的默认 grace timeout 和可配置上限需要基于现有 CLI 退出行为与真实 shell 测试确定；超时分类和“保留 owner”规则不可放宽。
