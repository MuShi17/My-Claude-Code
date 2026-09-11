## Purpose

规定本机 runtime 的统一 Application API、session/run/interaction 命令身份、状态和幂等边界，使 TUI、未来 GUI 与其他受控进程内调用方使用同一运行控制入口。

## ADDED Requirements

### Requirement: Application API 提供统一的运行控制入口

系统 MUST 提供进程内 Application API，至少覆盖 `session.create`、`session.list`、`run.start`、`run.status`、`run.cancel`、`interaction.respond` 与 `shutdown`。调用方 MUST 通过结构化请求和响应使用这些入口，不得依赖 Agent 私有字段、终端输出或 UI 按钮作为控制协议。

#### Scenario: 创建并查询会话

- **WHEN** 调用方以一个有效的 ProjectContext 请求 `session.create`，随后请求 `session.list`
- **THEN** 系统返回稳定的 session 身份，且列表只包含与该 workspace 绑定、可读取的会话记录

#### Scenario: 公开入口返回结构化运行状态

- **WHEN** 调用方提交 `run.start` 并轮询 `run.status`
- **THEN** 响应包含稳定的 session/run 身份、当前生命周期状态和可关联的结果信息，不要求解析终端文本或访问 Agent 私有字段

#### Scenario: 交互回复进入同一控制边界

- **WHEN** 调用方提交带 request/session/run/tool 身份的 `interaction.respond`
- **THEN** 回复由同一 Application 控制边界交给 C02 InteractionRegistry 校验，错误身份或已结束请求不能触发工具执行

### Requirement: 命令身份和重复命令必须幂等

每个会改变运行状态的命令 MUST 带有命令身份、作用域身份和参数摘要。相同作用域内相同命令身份与相同摘要的重复请求 MUST 返回首次请求的原结果且不得重复调度；相同命令身份但摘要不同 MUST 被拒绝。命令记录 MUST 能区分 session、run、interaction 和 owner 作用域。

#### Scenario: 重复 run.start 不重复启动

- **WHEN** 同一 session 以同一命令身份和相同参数摘要提交两次 `run.start`
- **THEN** 两次响应指向同一个 run，模型/工具 dispatch 最多发生一次

#### Scenario: 命令身份冲突被拒绝

- **WHEN** 已接受的命令身份再次携带不同参数摘要
- **THEN** 系统返回明确的冲突错误，保留首次命令结果，不创建第二个 run 或覆盖原记录

#### Scenario: cancel 与终态竞争只有一个结果

- **WHEN** `run.cancel` 与 run 的成功、失败或交互回复几乎同时到达
- **THEN** 控制边界只提交一个合法终态，所有重复或冲突命令都返回该终态的确定结果

### Requirement: 运行生命周期和状态查询必须诚实

Application MUST 为 queued、running、waiting_interaction、cancelling、succeeded、failed、cancelled、interrupted 和 uncertain 等状态定义可验证的转移约束；终态 MUST 单向且唯一。状态查询不得在尚未确认 OS 执行停止、持久化完成或关闭完成时伪造成功。

#### Scenario: 活跃 run 可观察交互等待

- **WHEN** run 等待一个 C02 InteractionRequest
- **THEN** `run.status` 返回 `waiting_interaction` 及可关联 request 身份，调用方可以提交回复或取消，而无需接触终端输入

#### Scenario: 未确认停止不报告成功

- **WHEN** shutdown 或 cancel 超时且受管理 shell、MCP 或持久化仍未确认完成
- **THEN** API 返回未完成信息或相应的 cancelling/interrupted/uncertain 状态，不返回成功终态

### Requirement: 同一 workspace 的 root run 由 Application 统一仲裁

Application MUST 以规范化 workspace 身份约束 active root run；同一 workspace 同时最多一个 active root run，child run MUST 绑定到该 root 的 owner 树。第二个 root 请求 MUST 被明确拒绝或返回已有 owner 信息，不得静默抢占。

#### Scenario: 同 workspace 第二个 root 被拒绝

- **WHEN** 两个受控 Application 调用方同时为同一规范化 workspace 请求 `run.start`
- **THEN** 只有一个调用方获得 root owner，另一个收到可关联的 owner-conflict 结果，且不会产生第二次 root dispatch

#### Scenario: 不同 workspace 可独立运行

- **WHEN** 两个 Application 调用方分别为两个不同规范化 workspace 请求 `run.start`
- **THEN** 两个 root 可以分别获得 owner，彼此的 session、控制记录和 dispatch 不互相覆盖
