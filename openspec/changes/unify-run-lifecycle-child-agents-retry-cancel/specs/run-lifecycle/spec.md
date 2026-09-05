## Purpose

统一用户 turn、主 run、子 Agent run、attempt 和各种终止路径的生命周期，使每个运行都可定位、可封存、可恢复，并禁止终态后的幽灵事件或不安全自动续跑。

## ADDED Requirements

### Requirement: Every turn and child run has layered identity

每个用户 chat turn MUST 创建一个可查询的主 run；每个子 Agent MUST 创建独立 run，并记录 parent run identity、turn/session identity 和 attempt 信息，子事件不能冒充父 run 事件。

#### Scenario: User starts a turn with a child

- **WHEN** 一个用户 turn 触发一个子 Agent
- **THEN** store 中存在 main run 与 child run，两者各有 lifecycle/terminal 事实且 child 带正确 parent_run_id

#### Scenario: Child finishes before parent

- **WHEN** child run 先完成而 parent 继续执行
- **THEN** child terminal 不封存 parent，parent 后续事件仍能按自身 run identity 读取

### Requirement: Run state transitions are guarded

run MUST 只允许从 open/running/awaiting-tool 等已定义非终态进入一个 terminal 状态；terminal 状态必须是 completed、failed、cancelled、aborted 或 budget_exceeded 之一，非法跃迁 MUST 被拒绝。

#### Scenario: Normal completion seals once

- **WHEN** main run 完成最后一个模型/工具步骤
- **THEN** 系统写入唯一 completed terminal 并封存 run，重复相同提交幂等

#### Scenario: Late event arrives

- **WHEN** run 已 terminal 后异步 provider/tool 回调抵达
- **THEN** guard 拒绝该事件或记录为独立诊断，不改变原 run 的 terminal

### Requirement: Retries are explicit and side-effect safe

重试 MUST 带有可关联的 attempt identity、原因和前次状态；系统 MUST 不自动重放 dispatch 已 durable 但 outcome 未知的工具副作用，也不得把新 attempt 覆盖为前次 attempt 的成功。

#### Scenario: Provider retry is explicit

- **WHEN** provider error 后调用方显式请求 retry
- **THEN** 新 attempt 与原 attempt 可区分并关联，前次错误保留，最终 run 状态反映所有 attempt

#### Scenario: Tool outcome is uncertain

- **WHEN** 工具 dispatch 已 durable 但进程在 outcome 前崩溃
- **THEN** recovery 将其标为 uncertain/open/failed 分类，系统不自动再次执行该副作用工具

### Requirement: Cancellation and abort produce terminal evidence

Ctrl+C、asyncio cancellation、provider abort、工具取消、权限拒绝和预算终止 MUST 进入可区分的终态路径，并尽最大努力 durable；终态处理 MUST 是幂等的。

#### Scenario: User presses Ctrl+C

- **WHEN** run 在模型流或工具执行中被 Ctrl+C 中断
- **THEN** run 最终可读为 cancelled 或 aborted，已发生事件保留，且不继续启动新的工具/模型调用

#### Scenario: Budget limit is hit

- **WHEN** max-cost 或 max-turns 限制被触发
- **THEN** 系统写入 budget_exceeded terminal，并阻止该 run 的后续副作用

### Requirement: Lifecycle failures are observable without changing public semantics

生命周期持久化失败 MUST 可诊断并遵循 canonical fail-closed policy；legacy flush/diagnostic 失败不得覆盖原始 cancellation/error，也不能让用户表面上看到已完成的 run。

#### Scenario: Terminal write is retried exactly

- **WHEN** terminal append 的调用方因不确定结果重试相同 terminal identity
- **THEN** store 保持一个 terminal，返回一致结果，不产生第二个终态

#### Scenario: Diagnostic sink fails during cancellation

- **WHEN** legacy sink 在取消处理期间失败
- **THEN** canonical terminal 仍按策略提交或报告失败，原 cancellation 语义和错误原因保持可见
