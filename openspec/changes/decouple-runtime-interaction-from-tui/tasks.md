> 状态说明：C02 实现已完成端口接线（全量 **446 passed**；套件含已知 flake，回归门见 §1.3）。输出端口、
> 交互端口、终端适配器、夹具与安全边界均已落地；实现缺口已由当前回归与独立 Gate 复核闭合。
> checklist 只勾选已按证据完成的事项。

本轮接任记录（2026-09-11）：C02 主体实现已落地，当前只继续真实调用路径守护、契约内缺失事件接线、局部回归和 Gate 证据；C03-C07、真实 Provider、Git 交付均不在范围内。

> 历史快照说明：顶部移交时状态行保留接任前的 446 passed 摘要；本轮当前状态、验收身份与证据以 §7 和批次总览 `change_registry.C02` 为准。

## 1. 变更准备与基线绑定

- [x] 1.1 绑定执行基线：execution_root=D:\workspace\My-Claude-Code、branch=main、base=21b9bdfbb7c0bf3b2f2f47f13890724cff76a5bc；runtimePython=`C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\python.exe`，Python 3.14.6 / pytest 9.1.1；唯一 writer=main-agent、无 mode_switch（2026-09-11 复核）。
- [x] 1.2 前置门复核：C01 的 accepted_result 与 contract_handoff 已登记（change_registry.C01），交接物为 C01 tasks §7 接口清单；C02 的 openspec_authorized / implementation_authorized 已由用户明确授权（2026-09-10）。
- [x] 1.3 冻结 C02 起始基线：`& $runtimePython -m pytest -q src/mini_claude/tests` 于 2026-09-11 退出码 0，`449 collected / 449 passed / 1 warning`；逐字状态与全部 SHA256 已记录于本任务文件 §1.3 证据块及移交快照。warning 家族为 `PytestUnraisableExceptionWarning`（`test_compaction_artifacts.py::test_artifact_archive_is_redacted_atomic_content_addressed_and_bounded`）。**套件非确定**：保留 3 条既有 flake 清单（`test_archive_projection` close、`test_local_consumers` CLI resume、`test_runtime_artifact_store_lifecycle` 大结果），回归门 MUST 采用"N 次重复全量 + flake 清单"，不得以单次全绿为唯一判据；禁止沿用 396/407 作为回归门。
- [x] 1.4 产出并记录 C02 独立测试策略：首轮双角色评审给出的 `GAP-C02-01`—`GAP-C02-15` 已逐条落到本 Item 与本文件；post-implementation 独立 delta 已完成代码与测试审查，证据包补齐后的正式 Gate 返回 `sufficient`。

### §1.3 冻结证据（2026-09-11）

```text
git status --porcelain=v1:
 M AGENTS.md
 M src/mini_claude/__main__.py
 M src/mini_claude/agent.py
 M src/mini_claude/mcp_client.py
 M src/mini_claude/memory.py
 M src/mini_claude/prompt.py
 M src/mini_claude/skills.py
 M src/mini_claude/subagent.py
 M src/mini_claude/tools.py
 M src/mini_claude/ui.py
 M src/pyproject.toml
?? openspec/changes/decouple-runtime-interaction-from-tui/
?? openspec/changes/introduce-project-context-isolation/
?? src/mini_claude/interactions.py
?? src/mini_claude/project_context.py
?? src/mini_claude/runtime_ports.py
?? src/mini_claude/tests/conftest.py
?? src/mini_claude/tests/provider_fixtures.py
?? src/mini_claude/tests/test_interaction_safety.py
?? src/mini_claude/tests/test_interactions.py
?? src/mini_claude/tests/test_project_context.py
?? src/mini_claude/tests/test_runtime_ports.py
?? src/mini_claude/tests/test_tui_adapter.py
?? src/mini_claude/tests/tool_fixtures.py
?? src/mini_claude/tui_adapter.py

SHA256:
AFC431B9306C0AE25880A854BD97EEF6A6544BA46DCC713E68B1D569F783195D  AGENTS.md
E6A45440B7B6B8096938FECE82A34E09416BCECFDA2CEEF5BA18C854E4341ABF  openspec/changes/decouple-runtime-interaction-from-tui/.openspec.yaml
B8B528A2EC4DC2EE196C970D5F808B3A9A1130F6826A92F9A8FB23AF97C4D97F  openspec/changes/decouple-runtime-interaction-from-tui/design.md
33F788DDC8DB8C417DB047C84404D4FE11D1AC9D7C8ADCB678F0F46C473B82B6  openspec/changes/decouple-runtime-interaction-from-tui/proposal.md
DA728F6DAA4C69FDE9345C522D6CDF198D1384B8A075020565A61745FEC90AD9  openspec/changes/decouple-runtime-interaction-from-tui/README.md
CB39267C9AC5105F781C5DA0A7E701F5D4461662436ABB3EBBD49B62C11E656F  openspec/changes/decouple-runtime-interaction-from-tui/specs/interactive-requests/spec.md
D0B6113EB72AED29F0E1F126BA1B6651E68CC1DD4BECAF00E3110C457F13024F  openspec/changes/decouple-runtime-interaction-from-tui/specs/runtime-output-ports/spec.md
C2D24F7A71A74E1CA84B93989D3BBCEB34109483F39DAC942A9265E8B8EDDDFB  openspec/changes/decouple-runtime-interaction-from-tui/tasks.md
2A52E9C6669BEAF79129ADB7D7843DB88CD99FE4E3A1ECE75BAA3DFC41DED489  openspec/changes/introduce-project-context-isolation/.openspec.yaml
525D8494FF6C7AC1D9D07D2CD7258AD1A9A1EB514BE7DCD93F64659897336F2E66  openspec/changes/introduce-project-context-isolation/design.md
FE172E3C3F6DA76E4813B2D8814FAAF21EE4D1A49CC493F547F8E5585D74E500  openspec/changes/introduce-project-context-isolation/proposal.md
FEE0E9D901009C74B0B6AD325839FF9AA3BC95CCE9AFF599D088E0F9C9AFFCF2  openspec/changes/introduce-project-context-isolation/README.md
174927B12D53B25FFE70B60B16B98510CF337C40A6622089DB1FD43E5FA71101  openspec/changes/introduce-project-context-isolation/specs/project-context/spec.md
5FE8B66F4626ED8876B58BB7A1F66B91FCB0389323B5153682F917CB965CF5E7  openspec/changes/introduce-project-context-isolation/tasks.md
BDD718E7BC9CA109E12AE0B776DF9A457DA570E62FABAF0E7465F69D11A8CB58  src/mini_claude/__main__.py
31CB53481F95F38B14F3514B002978DAE406EE386368FE6CC72046A77861087A  src/mini_claude/agent.py
DE7A27A4FB9417D76CC61D92F570F119A1FB46A4111ABA269896768CB51FE15E  src/mini_claude/interactions.py
687F51DD7C6128B44E3AAECB2C323B39FCA6FE9531AECF0F21F375E739DC5D6E  src/mini_claude/mcp_client.py
1D0B01F40A9C2A4ADCC5D27CEA1A6F8B72584E76C628498D21D9A155924EAEDF  src/mini_claude/memory.py
7D2C5AC18B907295D12BB5D0B31B8BF03D0B469C69E8CBCF4BC5F4CE9A95ED66  src/mini_claude/project_context.py
548066DDFDCC5BBB2E78436609B44A06D99DF8B2FAE995B6A71931EBD54CBED6  src/mini_claude/prompt.py
5E816492DCB716996ECA7FD5436696ACB36C035C0800A5B8C6F0CAC5ACB22266  src/mini_claude/runtime_ports.py
32D35AF887EAB0108A099B2486B193EA3C0A918EFC4A3D925FB187BED1852CAC  src/mini_claude/skills.py
E58682197621D9093BE3DF3EB9839CEFE1D379EA8A51B06D2CEDCAA05A860C50  src/mini_claude/subagent.py
391F88EECEA419F14A2D539908EEAED5499D67A2DCE143A0C2DC620E74458BEB  src/mini_claude/tests/conftest.py
118827657BCB8E0121A68A6EB12099176DD482DE7D25E6A5A23CF12912C6FA64  src/mini_claude/tests/provider_fixtures.py
2FF65493E9523F36EEF7B227B34071AE01FC08E475B6B12FBC99A1E4C4653F80  src/mini_claude/tests/test_interaction_safety.py
B04435085F3BB2BB458C2C0FDC8F98FCBD8D3EA3CFA7101EBC8B7783783BD343  src/mini_claude/tests/test_interactions.py
6B811779ED89A4D4F094DCF1F2C0765D91B065F6C405D59CCF3FD0BB490CFF20  src/mini_claude/tests/test_project_context.py
2619C42DA7AAF2FB244A27DE73FC45D6D6F40D0DABF19E9BDF83E7AA106F562C  src/mini_claude/tests/test_runtime_ports.py
04FF4C15CB39382629C17C67AA1F15A340B28A7279FE95A6DAE80EEA85D95F75  src/mini_claude/tests/test_tui_adapter.py
DFFE1D8B06CB78F708546161C991C3B69DA19B2F25FFFA3D6A693C8140E8E189  src/mini_claude/tests/tool_fixtures.py
BCD4D3BF534DD6EDA680D8E6FD5ECAA0665EE165BA4A1AFF039C49430F371E29  src/mini_claude/tools.py
30A2AD65EEEE14553C86E14813AB8760985FDC50666BE9325B19A1B141C1FDC4  src/mini_claude/tui_adapter.py
17EBFF2079BC15B81A952A8A864600E70BC9AD1B54054849C85185F5F6BFA7F7  src/mini_claude/ui.py
5AF20B72517167E6084E9846EB51FF839629B67887119B01B5DD7FB4ABAAEF79  src/pyproject.toml
```

### §1.4 测试策略 delta（独立 Gate 已完成）

本轮按真实调用链闭合既有 Gap：真实 `Agent.chat()` 的工具调用/deny/error/default-port/canonical 终态；真实 Anthropic/OpenAI stream 的 thinking、budget、lifecycle 和敏感载荷扫描；真实 CLI one-shot 与 REPL 构造点端口注入；agent-fork/skill-fork 两个子 Agent 的双端口继承；两个 pending、跨 run/tool 回复、审批/取消两种顺序；批准后的 deny/mtime 复核；端口失败隔离。测试只使用本地假 Provider 或 SDK 边界替身，不调用真实远端 Provider；结论须经只读独立 `test-strategy-agent` Gap Closure 后才能把 1.4 和 D(C02) 标为完成。

本轮实现核对（2026-09-11）：`Agent.chat()` 的通用 Provider 异常现在同时发布 `error` 输出事件与既有 `chat_error` 观测；OpenAI 正常无工具结束路径补齐 `lifecycle(phase=turn_complete)`，与 Anthropic 路径及输出端口契约一致。交互回复新增可选 session/run/tool 身份匹配校验：不改变省略身份字段的既有调用方式，但同摘要的显式 foreign identity 必须被拒绝；审批返回后，两个 Provider 路径会再次检查当前 deny/plan 策略，才进入 durable dispatch。`Agent(provider_client=...)` 为离线 SDK-compatible 边界替身提供公共构造注入点。

本轮测试接线（2026-09-11）：新增本地 SDK MockTransport 驱动的双 Provider public `Agent.chat()` 输出事件回归（thinking/text/budget/turn_complete/身份/敏感载荷/canonical terminal），补充真实工具拒绝路径的 `tool_call_id` 断言、Provider 异常和缺省端口的 public 路径断言、one-shot/REPL 构造点注入、skill-fork 双端口、双 pending/显式 foreign identity，以及批准后的当前策略与外部文件创建保护。另补 public Provider 构造注入、OpenAI 双顺序工具调用 ID、真实 `Agent.chat()` 审批/取消两种到达顺序的至多一次 dispatch；端口失败在 public `Agent.chat()` 上的 canonical 等价已通过。

focused 回归修复（2026-09-11）：参数化双 Provider 拒绝路径暴露 Anthropic “确认被拒”分支遗漏 `tool_denied` 输出事件；已与 OpenAI 分支对齐，保持 durable deny 与结构化观察事件同时存在。

GapClosure 补测（2026-09-11）：新增 public `Agent.chat()` 端口失败与 canonical 终态等价、双 Provider 批准后策略复核、外部创建文件保护、屏障化 approve/cancel 竞争，以及带显式 session/run/tool 身份的同摘要 foreign reply 拒绝；独立 post-implementation Gate 已确认这些行为证据闭合。

测试断言校正（2026-09-11）：端口失败等价比较只保留 canonical 业务顺序/终态/内容类别，忽略每次运行必然变化的 attempt 元数据；approve-first 仍验证无请求被取消，同时接受控制面通知端口的既有行为。

## 2. 输出端口（I04）

- [x] 2.1 新增 `src/mini_claude/runtime_ports.py`：定义 `OutputEvent`（携带 `session_id`/`run_id`/可选 `attempt_id`/`tool_call_id`/`stream`）、`OutputPort` 协议、`NullOutputPort`（不写业务输出）、`RecordingOutputPort`（语义序列记录）与 `emit_safely`（端口失败隔离 + 有界诊断环）。**终端实现不在此文件**：Rich 渲染由 I09A 的 `tui_adapter.py` 承载（见 §4.1）。
- [x] 2.2 迁移**全部**输出调用点（范围已按评审 GAP-C02-01/02 扩展）：`agent.py` 的 12 个 `ui.print_*` 家族（43 处）→ `self._out_*`；4 处绕过 `ui.py` 的裸 `print`（`[mcp] Init failed`、`[runtime] … failed`）→ `self._out_raw`；`memory.py`/`mcp_client.py` 的 3 处后台诊断 → `emit_diagnostic`。**runtime 模块（agent/interactions/runtime_ports/tools/memory/mcp_client）已无裸 `print(`**；入口与适配器层（`__main__.py` 的 REPL 提示与 plan 审批选项、`ui.py`）仍保留终端 `print`，属终端层职责；`_emit_text` 的 `_output_buffer` 语义保留（CLI 消费者用例通过）。
- [x] 2.3 端口只作为观察接口：canonical 持久化仍走既有 emitter/store；端口抛错由 `emit_safely` 捕获并降级为有界诊断环（`port_diagnostics()`），不改写终态与 provider 消息。
- [x] 2.4 新增 `src/mini_claude/tests/tool_fixtures.py`（承接 GAP-I02-06）：四种夹具均具**真实语义**——`PausableTool` 真正挂起（线程事件 + `wait_started`）、`CancellableShellTool` 持有真实子进程句柄并可真正终止、`LateResultTool` 在后台线程返回不阻塞事件循环、`LongOutputTool` 确定性长输出。用例：`test_pausable_fixture_really_blocks_until_released`、`test_cancellable_shell_fixture_really_terminates_process`、`test_late_result_fixture_does_not_block_caller`。
- [x] 2.5 新增 `src/mini_claude/tests/test_runtime_ports.py`：已由真实 public `Agent.chat()` 驱动 headless/Provider error/default-port/canonical 等价/子 Agent 归属断言；端口失败不会改变 canonical 业务形状或 Provider 请求，夹具语义与敏感标记均有回归。证据：`test_runtime_ports.py`、`test_local_consumers.py`，C02 focused 65 passed。
- [x] 2.6 新增可控 **Provider** 注入点（承接 items/02 的 C02 义务，GAP-C02-10）：`Agent(provider_client=...)` 保持 SDK-compatible 构造点，`tests/provider_fixtures.py` 提供可暂停/抛错/长输出/晚到结果四形态；public 双 Provider 路径通过本地 MockTransport，OpenAI 顺序工具 ID 另有 mutation-killer 断言。证据：`test_runtime_ports.py`、`test_local_consumers.py`，不调用真实远端 Provider。

## 3. 交互端口（I05）

- [x] 3.1 新增 `src/mini_claude/interactions.py`：`InteractionRequest`（不可变身份）、`InteractionReply`、`InteractionRegistry`（**同步**状态转移 → 单一控制序列化边界）。
- [x] 3.2 异步等待：`InteractionPort.request` 为 awaitable（`RecordingInteractionPort(hold=True)` 模拟挂起）；取消由 `Agent.cancel_pending_interactions()` 提供，等待期间取消会把请求转 `cancelled` 并重新抛出 `CancelledError`；用例 `test_hold_port_wait_is_cancellable_without_blocking_control_plane`、`test_cancel_pending_interactions_is_the_control_plane_entry`。
- [x] 3.3 移除 runtime 内终端 `input` fallback：`_confirm_dangerous` 改为「显式 `confirm_fn` → 交互端口（默认 `DenyingInteractionPort`）」；全仓库 runtime 模块已无 `input(`（实测扫描为空）；用例 `test_agent_confirm_does_not_fall_back_to_terminal`（monkeypatch `builtins.input` 为断言失败）。
- [x] 3.4 幂等与冲突：相同回复返回原对象（幂等）、冲突回复 `ReplyConflictError`、参数摘要不匹配拒绝且状态不变；`cancel_all` 只影响 pending，不改写已 resolved。
- [x] 3.5 保留 `Agent.confirm_fn` 兼容入口并优先于端口；用例 `test_confirm_fn_remains_supported`。
- [x] 3.6 批准不绕过规则：执行前重校验参数、deny 策略与 mtime；双 Provider public run 覆盖批准后 deny、外部创建和既有文件外部修改，未调用 mock `check_permission`。
- [x] 3.7 交互用例已覆盖：参数篡改、未知请求、重复/冲突回复、过期、单向终态、取消、提问不授权、两个同时 pending、显式跨 run/tool 身份拒绝、审批/取消屏障与真实 public dispatch 至多一次；旧端口省略身份的兼容幂等也有断言。

## 4. TUI 适配（I09A）

- [x] 4.1 新增 `src/mini_claude/tui_adapter.py`：`TerminalOutputPort`（13 类事件 → 既有 `ui.*` 渲染；诊断走 stderr）与 `TerminalInteractionPort`（终端提示 + **可取消的等待**：阻塞读取经 executor，事件循环侧轮询 `cancel_event`，取消可在有限时间内解除等待且不阻塞事件循环；`input_fn` 可注入）。
- [x] 4.2 `__main__.py` 在 REPL 与 one-shot 两种模式都注入输出/交互端口；CLI flags、one-shot/REPL/resume 与既有权限模式保持不变（CLI `--help`/`--list` exit 0，CLI smoke 与本地消费者用例全绿）。
- [x] 4.3 新增 `src/mini_claude/tests/test_tui_adapter.py`（12 项）：逐类事件路由的语义断言（非 ANSI 快照）、诊断走 stderr、Agent 缺省端口静默、注入端口后事件可观察、裸诊断不经 stdout、工具事件带 `tool_call_id`、预算超限发 `error`、思考/生命周期事件辅助、终端交互不阻塞事件循环、非 y 拒绝、EOF 安全拒绝、**取消解除等待**。
- [x] 4.4 补书面手工 TUI 验证脚本与预期观察清单（承接 GAP-I02-09）：见 items/09 的「手工 TUI 验证脚本」小节（T1—T10 与 A1—A4，含启动命令、输入、预期观察与失败判定）。

## 5. 验证与局部验收

- [x] 5.1 `& $runtimePython -m pytest -q src/mini_claude/tests/test_runtime_ports.py src/mini_claude/tests/test_local_consumers.py src/mini_claude/tests/test_provider_content.py`：退出码 0，65 passed，23.15s。
- [x] 5.2 `& $runtimePython -m pytest -q src/mini_claude/tests/test_interactions.py src/mini_claude/tests/test_tool_result_boundary.py`：退出码 0，40 passed，4.02s。
- [x] 5.3 `& $runtimePython -m pytest -q src/mini_claude/tests/test_cli_smoke.py src/mini_claude/tests/test_tui_adapter.py src/mini_claude/tests/test_local_consumers.py`：退出码 0，56 passed，2 warnings，25.03s；告警为 Windows Proactor 管道析构，未出现 failed/error。
- [x] 5.4 全量回归 `& $runtimePython -m pytest -q src/mini_claude/tests`：连续两次均退出码 0，`476 passed / 2 warnings`（48.69s、48.84s），passed 高于 §1.3 的 449；告警均为同一 `test_compaction_artifacts.py::test_artifact_archive_is_redacted_atomic_content_addressed_and_bounded` 的 `PytestUnraisableExceptionWarning` 家族，无 failed/error。
- [x] 5.5 `git diff --check` 与两个 OpenSpec change 的 `--type change --strict` 均通过；另对 37 个当前 tracked/untracked 文件完成 SHA256 清单与未跟踪文件边界核验。`git diff --check` 仅报告 Git 的 LF→CRLF 提示，不是 whitespace error。
- [x] 5.6 完成 V(C02)：独立冻结差异审查、风险对应 GapClosure、`contract_handoff` 与唯一台账回写均已完成；Hooke 的独立 delta Gate 返回 `sufficient`，test_gap_closure_result=`closed`。result_identity、product diff provenance、changed-item mapping、retained Gap inventory 见 §7，交付动作保持 none。

## 6. 跨 Change 影响（承接 C01 §7）

- [x] 6.1 保持 C01 交接的接口不被回退，并给出**可证伪断言**（不再只写"回归用例覆盖"）：
  - `Agent(..., project_context=...)` 与 `Agent.context`：`test_agent_tool_subagent_inherits_parent_context` 断言子 Agent 收到的 `project_context.root` 与父一致；
  - `execute_tool_value(..., context=)` 与工具相对路径基准：`test_tool_dispatch_uses_context_not_process_cwd`、`test_file_tools_resolve_relative_paths_against_context`；
  - `check_permission(..., context=)` 与规则来源：`test_permission_rules_come_from_context_workspace`、`test_permission_rule_path_matching_is_basis_independent`；
  - `load_permission_rules(context=)` 按 `workspace_id` 分键：`test_permission_rule_path_matching_uses_context_root`。
  本次改造只新增端口参数与适配器，未触碰上述路径（回归门见 §1.3 的重复运行判据）。
- [x] 6.2 产出自 Change 的接口交接清单（供 C03/C05）：
  - **输出端口**：`runtime_ports.OutputEvent`（`kind`/`session_id`/`run_id`/`attempt_id`/`tool_call_id`/`stream`/`payload`）、`OutputPort.emit`、`NullOutputPort`、`RecordingOutputPort`、`emit_safely`（失败隔离 + `port_diagnostics()`）；`Agent(output_port=...)` 与 `Agent.output_port`。
  - **交互端口**：`interactions.InteractionRequest`（不可变身份）、`InteractionReply`（含 `params_digest` 绑定）、`InteractionRegistry`（同步状态机：pending → resolved | expired | cancelled，幂等与冲突规则）、`InteractionPort`、`DenyingInteractionPort`；`Agent(interaction_port=...)`、`Agent.interaction_registry`、`Agent.cancel_pending_interactions()`。
  - **持久化/快照端口契约（交 C03 实现）**：C03 MUST 提供请求与控制记录的持久化与快照接口，使 `InteractionRegistry` 的状态可在进程重启后恢复；C02 只定义**进程内**语义，MUST NOT 被理解为已具备跨进程/崩溃恢复。C05 MUST 在 host 侧把 `OutputPort`/`InteractionPort` 映射为 wire 事件与 `interaction.respond` 命令。
  - **终端适配器**：`tui_adapter.TerminalOutputPort`（事件 → `ui.*` 渲染；诊断走 stderr）；I09A 将继续在此扩展交互适配（终端 `input` 只允许出现在适配器内）。

## 7. C02 当前验收包与交接（2026-09-11）

本节是 C02 当前复核输入与 V(C02) 回写的唯一证据块；移交说明中的历史快照不覆盖本节。下列记录只描述 C02，不授权 C03-C07 或任何交付动作。

- **authority / contract**：`canonical_task_source=00-批次总览.md`；`primary_change=C02`；`actual_change_id=decouple-runtime-interaction-from-tui`；`contract_revision=gui-batch-plan-v2/C02-v1`；`execution_root=D:\workspace\My-Claude-Code`；`branch=main`；`base=21b9bdfbb7c0bf3b2f2f47f13890724cff76a5bc`；`writer_owner=main-agent`；`mode_switch=none`。
- **result_identity**：`C02-V-20260911-01`；`product_diff_digest=sha256:2666B452D5A764BEE0F0A2FF2F28B65760717239026F6094943D4132EFEB1E75`；这是当前工作树 `git status --porcelain=v1 -z` 结果中所有 `src/` tracked/untracked 文件的只读源码 manifest，不是 commit，包含 C01 与 C02 source diff，不包含 AGENTS Harness marker 与本 KB 文档。
- **manifest serialization**：按 `git status --porcelain=v1 -z` 解析路径；仅保留 `src/` 路径；若记录是未跟踪目录则递归展开其中的文件；按仓库相对路径（统一 `/`）去重并按字典序排序；每条记录为 `relative/path|UPPERCASE_SHA256`；以单个 LF 连接且末尾无 LF；对该 UTF-8 字节串计算 SHA256。下面是本次 `files=23` 的完整明细，可独立复算上面的 digest：

```text
src/mini_claude/__main__.py|BDD718E7BC9CA109E12AE0B776DF9A457DA570E62FABAF0E7465F69D11A8CB58
src/mini_claude/agent.py|CF81B07CF58D6E1B9B602A21317A140C1216C7212720C45C0CF44A4D573A3B5C
src/mini_claude/interactions.py|9C16972E1FAF208BDC9E805BE6ACAEC4F08949616D626EE478FF0B78057F14E0
src/mini_claude/mcp_client.py|687F51DD7C6128B44E3AAECB2C323B39FCA6FE9531AECF0F21F375E739DC5D6E
src/mini_claude/memory.py|1D0B01F40A9C2A4ADCC5D27CEA1A6F8B72584E76C628498D21D9A155924EAEDF
src/mini_claude/project_context.py|7D2C5AC18B907295D12BB5D0B31B8BF03D0B469C69E8CBCF4BC5F4CE9A95ED66
src/mini_claude/prompt.py|548066DDFDCC5BBB2E78436609B44A06D99DF8B2FAE995B6A71931EBD54CBED6
src/mini_claude/runtime_ports.py|5E816492DCB716996ECA7FD5436696ACB36C035C0800A5B8C6F0CAC5ACB22266
src/mini_claude/skills.py|32D35AF887EAB0108A099B2486B193EA3C0A918EFC4A3D925FB187BED1852CAC
src/mini_claude/subagent.py|E58682197621D9093BE3DF3EB9839CEFE1D379EA8A51B06D2CEDCAA05A860C50
src/mini_claude/tests/conftest.py|391F88EECEA419F14A2D539908EEAED5499D67A2DCE143A0C2DC620E74458BEB
src/mini_claude/tests/provider_fixtures.py|118827657BCB8E0121A68A6EB12099176DD482DE7D25E6A5A23CF12912C6FA64
src/mini_claude/tests/test_interaction_safety.py|C0377790C0C0DF38DD4185797F96267014ABD838DE343EC1D6DCE6A79653FE7B
src/mini_claude/tests/test_interactions.py|0FBED25528DA0A752AAA6D20C75825E54050971627B732B235B792E72E8D3087
src/mini_claude/tests/test_local_consumers.py|C91794250DF306778B633AA7C90F4CC134CD07533131A3AAB41588BB0E6055B7
src/mini_claude/tests/test_project_context.py|6B811779ED89A4D4F094DCF1F2C0765D91B065F6C405D59CCF3FD0BB490CFF20
src/mini_claude/tests/test_runtime_ports.py|9933F5993F950F6CBF068609867BDBB91C146801D6253B8A322C6A35F9975F34
src/mini_claude/tests/test_tui_adapter.py|D4FE5D9EDE8F1AFFD1E02CF4D2170A29AA14E735D8F4C3078ACE59E5E9193B65
src/mini_claude/tests/tool_fixtures.py|DFFE1D8B06CB78F708546161C991C3B69DA19B2F25FFFA3D6A693C8140E8E189
src/mini_claude/tools.py|BCD4D3BF534DD6EDA680D8E6FD5ECAA0665EE165BA4A1AFF039C49430F371E29
src/mini_claude/tui_adapter.py|30A2AD65EEEE14553C86E14813AB8760985FDC50666BE9325B19A1B141C1FDC4
src/mini_claude/ui.py|17EBFF2079BC15B81A952A8A864600E70BC9AD1B54054849C85185F5F6BFA7F7
src/pyproject.toml|5AF20B72517167E6084E9846EB51FF839629B67887119B01B5DD7FB4ABAAEF79
```

- **changed-item mapping**：I04 → `runtime_ports.py`、`agent.py`/后台诊断输出接线、`provider_fixtures.py`、`test_runtime_ports.py` 与双 Provider public consumer tests；I05 → `interactions.py`、Agent 审批/取消/策略复核、`test_interactions.py`、`test_interaction_safety.py`；I09A → `tui_adapter.py`、`__main__.py`、`test_tui_adapter.py` 与 CLI consumer tests；C02 OpenSpec 的 `design.md`/`tasks.md` 记录契约与验证证据；C03-C07 无代码变更。
- **acceptance matrix**：结构化输出、事件身份/敏感载荷、默认端口、Provider error、端口失败隔离、子 Agent 归属、交互身份/状态/双 pending/竞态、批准后 deny/mtime、CLI one-shot/REPL/fork、OpenAI 顺序 tool-call-id 均由 public 或真实 Agent 调用链断言；Provider error 的 canonical 失败终态为 `error(metadata.lifecycle=provider_error,status=failed)`，`chat_error` 仅为既有观察钩子，不制造第二终态。
- **Gap inventory（经独立 Gate 分类）**：`closed_gap_ids=GAP-C02-01—16,GAP-C02-18,GAP-C02-19`；`retained_gap_ids=GAP-C02-17`（已登记的套件非确定性/Windows warning 风险，不是本轮功能阻塞）；`remaining_gap_ids=[]`；`new_gap_ids=[]`。C03 持久化/快照/重启恢复/owner 锁、C05 host wire 映射，以及真实远端 Provider、Git/发布/部署/清理均是明确排除项，不列为 C02 遗留 Gap。
- **verification**：C02 focused 三组分别为 `65 passed`、`40 passed`、`56 passed / 2 warnings`；全量连续两次均为 `476 passed / 2 warnings`，无 failed/error。OpenSpec C01/C02 `--type change --strict` 均 valid；`git diff --check` 无 whitespace error，仅有 LF→CRLF 提示；本节 manifest 为 23 个 `src/` 路径；runtime 模块 `print(`/`input(` 扫描无命中，入口/适配器层保留终端职责。
- **independent Gate**：前一轮 `test-strategy-agent` delta 复核因缺少实际 §7 与可复算 manifest 返回 `blocked_by_missing_input`；补齐后同一 Hooke 任务重新读取并返回 `sufficient`，`test_gap_closure_result=closed`，完成 Gap 分类。主 Agent 已重新检查当前差异身份、OpenSpec strict 与 Gate 结论，`acceptance_state=local_acceptance_passed`；不因此授权进入 C03。
- **contract_handoff（已确认，非交付授权）**：C03/C05 以本文件 §6.2 为输入，使用 `OutputEvent`/`OutputPort`、`InteractionRequest`/`InteractionReply`/`InteractionRegistry`、`Agent` 端口与取消入口、TUI 适配器边界。C03 MUST 另行实现交互请求/控制记录持久化、快照、重启恢复与 owner 锁；C05 MUST 另行实现 host 侧 output/interaction wire 映射和 `interaction.respond` 命令；C02 仅定义并验证进程内语义。
