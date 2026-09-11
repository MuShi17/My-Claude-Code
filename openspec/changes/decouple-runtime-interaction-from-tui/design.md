## Context

C01 已把 workspace 上下文显式化（`ProjectContext`、消费方 `context=` 接线、按 `workspace_id` 分键的缓存），并留下跨 Change 接口交接清单（C01 tasks §7）。本 Change 处理下一层耦合：**观察面与交互面仍绑在终端上**。

实测的现状依赖点（符号名为准，行号会漂移）：

- `agent.py` 从 `.ui` 直接导入并在约 30 处调用 `print_assistant_text` / `print_tool_call` / `print_tool_result` / `print_confirmation` / `print_divider` / `print_cost` / `print_retry` / `print_info` / `print_sub_agent_start` / `print_sub_agent_end` / `start_spinner` / `stop_spinner`；`ui.py` 依赖 `rich`，使用模块级全局 spinner（含 `\r\033[K` 直写 `sys.stdout`）与 `sys.stdout`。
- **另有 6 处绕过 `ui.py` 的原始 `print`**（评审实测补入）：`agent.py` 的 `[mcp] Init failed…` 与 `[runtime] … failed`（共 4 处）、`memory.py` 的 `[memory] semantic recall failed…`、`mcp_client.py` 的 `[mcp] Connected/Failed…`。这些同样属于"观察面绑在终端上"，迁移范围必须覆盖（GAP-C02-01）。
- `run_once` / `_emit_text` 依赖 `_output_buffer` 语义，`test_cli_smoke.py` 也依赖它；迁移必须保留该行为（GAP-C02-02）。
- 审批/提问路径依赖终端读取：`Agent._confirm_dangerous` 有阻塞式 `input("  Allow? (y/n): ")` fallback；plan 模式的终端交互在 `__main__.py`（经注入的 `_plan_approval_fn`），不在 `agent.py` 内。
- `print_error` 在 `agent.py` 只被导入、实际由 `__main__.py` 调用（工件中不得声称 agent.py 通过它输出错误）。

约束：CLI/TUI 行为与既有 flags 必须保持不变；权限语义（allow/deny/plan/dontAsk 与先读后改）不得改变；canonical 持久化仍由既有 emitter/store 承担；不实现持久化交互、owner 锁、Application API 与 stdio host（属 C03/C05）。

## Goals / Non-Goals

- Goals：runtime 具备**结构化输出端口**与**人工交互端口**，无终端时可运行；TUI 成为端口适配器且行为不变；等待人工输入不阻塞取消与查询；审批回复幂等、失效即拒；批准不绕过既有规则。
- Non-Goals：持久化交互请求与崩溃恢复（C03）、跨进程交互协议（C05）、owner 锁与单 writer 语义（C03）、GUI 展示（C06）、把 `ui.py` 重写为通用渲染框架。

## Decisions

### D1 端口以显式参数注入，缺省用安全默认实现（备选：进程级全局端口注册表）

`Agent(..., output_port=..., interaction_port=...)` 接收端口；未提供时使用 `runtime_ports.NullOutputPort`（不写业务输出）与 `interactions.DenyingInteractionPort`（对需要交互的调用保守拒绝或走既有非交互策略）。备选「模块级全局端口 + 注册函数」被否决：C01 已因进程级可变上下文单例被评审否决（D1），同类问题会再次复活"最后一次 set 生效"的隐式状态。

### D2 输出端口是观察接口，canonical 写入路径不变（备选：让端口参与持久化）

端口只接收事件，不返回决策、不参与 canonical 写入；端口抛错被捕获并降级为诊断事件。备选「端口决定是否写入 store」被否决：会把展示层与事实源绑在一起，违反"canonical 是唯一事实源"。

### D3 交互请求用不可变身份 + 单向状态机（备选：可变 dict + 前端去重）

`InteractionRequest` 绑定 `request_id`、`session_id`、`run_id`、`tool_call_id`、`request_kind`、`params_digest`；`InteractionReply` 若携带对应身份字段，也必须逐项匹配请求，不能只凭相同摘要跨 run/tool 复用；省略字段保留既有端口兼容性。状态只允许 `pending → resolved | expired | cancelled`，终态不可再变。备选「可变 dict + 由前端按钮禁用保证幂等」被否决：批次概念模型明确要求幂等由 runtime 保证。

### D4 等待基于可取消的异步等待对象（备选：阻塞式终端读取）

端口返回 awaitable；等待期间事件循环仍可处理 cancel 与查询。终端适配器把 `input` 放进 executor 或等价机制，避免阻塞事件循环。备选「阻塞 input」被否决：会冻结控制面，使 C03 的取消无从下手。

### D5 批准不构成授权（备选：批准即放行）

执行前仍走 `check_permission(context=...)` 与先读后改 mtime 校验；批准只是"用户同意继续"，不是绕过规则的凭据。备选被否决：批次与任务卡均要求 GUI/适配器不得绕过既有策略。

### D6 稳定语义断言，不做 ANSI 快照（备选：对渲染输出做字节快照）

TUI 适配器测试断言**语义事件序列**（哪些事件、带什么身份与关键字段），不断言颜色/转义细节。备选被否决：ANSI 快照脆弱且与实现细节耦合。

### D7 GAP-I02-06 夹具归属

可控工具夹具落在 `src/mini_claude/tests/tool_fixtures.py`，由 I04 建立并供 C02 的 I05/I09A 与后续 Change（C03 的取消、C05 的慢消费者）复用；夹具包含可暂停、长输出、可取消 shell、晚到结果四种形态。

### D8 Provider 边界注入

`Agent` 增加可选的 keyword-only `provider_client` 构造参数。未提供时保持现有
`api_base`/`api_key` SDK 创建路径；提供时只替换当前后端使用的 SDK-compatible
client，不改变 runtime 的 canonical、权限或端口语义。该注入点服务本地离线
消费者和可控边界夹具，不代表调用真实远端 Provider，也不扩展 C02 的交付范围。

## Risks / Trade-offs

- [端口注入面扩大，可能破坏既有调用方] → 端口参数全部 keyword-only 且带安全默认；回归门以**每个实施小节重冻的全量基线**为准（C02 起始为 449 passed，不得沿用 C01 的 396）。
- [移除终端 fallback 后，既有 CLI 交互路径行为变化] → 终端行为整体下沉到 `tui_adapter.py`，CLI 入口显式注入该适配器；用 CLI smoke 与 TUI 手工脚本验证等价。
- [等待取消存在竞态] → 在同一控制序列化边界内处理 respond/cancel/expire，并以幂等规则固定唯一结果（用例覆盖竞争）。
- [规则语义被适配器绕过] → 执行前重新校验参数、deny 规则与 mtime；用例固定"批准后仍被 deny 拦截"与"批准后文件被改"。
- [无终端默认实现可能静默丢信息] → 默认端口显式记录"未注入端口"诊断，而不是伪装成成功。

## Migration Plan

1. C02 变更准备（本文件、specs、tasks）→ 独立设计评审 + 本 Change 测试策略。
2. I04：建立 `runtime_ports.py` 与 `tool_fixtures.py`，迁移输出调用点，补 headless 与身份断言用例。
3. I05：建立 `interactions.py`，迁移审批/提问接线并移除 runtime 终端 fallback，补状态机/幂等/竞争/失效用例。
4. I09A：建立 `tui_adapter.py`，让 CLI/TUI 注入适配器，补语义断言与手工 TUI 脚本（GAP-I02-09）。
5. 回滚策略：端口为新增文件 + 接线点，回退即恢复 `ui.py` 直连路径；不涉及数据与 schema 变更。

## Open Questions

- `Agent.confirm_fn` 是否保留为兼容入口（转发到交互端口）还是标记弃用？当前倾向"保留并转发"，以免破坏既有测试与第三方调用。
- plan 模式下的交互分支是否也走交互端口？倾向"是"，但需确认与 plan 文件写入的边界。
- 追问/附带问题（question 的多轮）是否属本 Change？倾向"仅单轮请求-回答"，多轮归 C03。
