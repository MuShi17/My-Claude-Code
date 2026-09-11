## Why

当前 runtime 的观察面与交互面直接绑在终端上：`agent.py` 通过 `ui.py`（Rich、全局 spinner、`sys.stdout`）打印文本/思考/工具状态/预算/错误，审批与提问则依赖终端 `input`。这让"无终端调用方"（未来的 TUI 端口适配、stdio host、GUI）无法驱动同一个 runtime，也无法在不改写 canonical 事实的前提下替换展示层。C02 把输出与人工交互抽成显式端口，使 runtime 在无终端时仍可运行，同时保持既有 CLI/TUI 行为不变。

## What Changes

- 新增 `src/mini_claude/runtime_ports.py`：定义**结构化输出端口**（文本/思考、工具状态、预算、错误、子 Agent 归属、生命周期）与默认的终端适配器实现；端口是**观察接口**，不参与 canonical 持久化。
- 新增 `src/mini_claude/interactions.py`：定义**人工交互端口**（审批与提问），带 `request_id`、绑定 session/run/tool_call/参数摘要、状态机（pending/resolved/expired/cancelled）与异步等待对象；终端输入成为该端口的适配器实现。
- `agent.py`、`subagent.py`、`tools.py` 的审批接线改为经端口调用；**移除 runtime 内的终端 `input` fallback**（终端输入只存在于适配器）。
- 新增 `src/mini_claude/tui_adapter.py`：把既有 TUI 行为（Rich 渲染、终端输入、Ctrl+C/EOF 语义、技能命令）实现为端口的消费方；不重写渲染层，不做大规模重命名。
- 新增 `src/mini_claude/tests/tool_fixtures.py`：承接 GAP-I02-06 的可控工具夹具（可暂停、长输出、可取消 shell、晚到结果），供 C02 测试矩阵与后续 Change 复用。
- 不改变既有权限语义（allow/deny/plan/dontAsk 与先读后改保护不变）；不实现持久化交互、Application API、owner 锁或 stdio host（分别属 C03/C05）。

## Capabilities

### New Capabilities

- `runtime-output-ports`: 结构化输出端口的契约与语义（字段、身份、生命周期、失败隔离），以及"端口是观察接口、不写 canonical"的边界。
- `interactive-requests`: 人工交互请求的契约与语义（身份绑定、状态机、异步等待、失效与幂等规则），以及"回答不等于工具授权"的约束。

### Modified Capabilities

（无。`openspec/specs/` 当前只有 `.gitkeep`，没有已归档 main spec；本 Change 不依赖也不修改既有 capability 的 requirement。）

## Impact

- 新增：`runtime_ports.py`、`interactions.py`、`tui_adapter.py`、`tests/test_runtime_ports.py`、`tests/test_interactions.py`、`tests/test_interaction_safety.py`、`tests/test_tui_adapter.py`、`tests/tool_fixtures.py`、`tests/provider_fixtures.py`。
- 修改：`agent.py`（输出与审批改经端口；删除对 `ui` 的 13 个死导入与终端 `input` 回退）、`tools.py`（审批接线 + **`execute_tool` 边界内强制权限策略**）、`memory.py` 与 `mcp_client.py`（后台诊断改走 `emit_diagnostic`）、`ui.py`（新增 `print_diagnostic`）、`__main__.py`（构造并注入输出/交互端口与诊断接收器；REPL 不再注册 `confirm_fn`）。`subagent.py` 未改动。
- 依赖：不引入新第三方依赖；`rich` 仍是适配器层依赖，runtime 模块不再直接依赖它。
- 消费者与重新验证集合（承接 C01 tasks §7）：`Agent(..., project_context=...)` 与 `Agent.context` 的接线必须保持；`execute_tool_value`/`check_permission` 的 `context=` 透传不得回退；CLI/TUI 行为与 flags 保持不变；Harbor 入口离线可用。
- 非目标：持久化交互与重放（C03）、owner 锁与取消拥有权（C03）、stdio host 与跨进程协议（C05）、桌面 GUI（C06）。
