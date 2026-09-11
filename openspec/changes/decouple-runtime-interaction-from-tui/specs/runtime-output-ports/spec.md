## Purpose

规定 runtime 结构化输出端口的契约与语义：事件形态与身份、端口作为观察接口的边界、以及端口失败时不得影响 canonical 事实与 run 终态。

## ADDED Requirements

### Requirement: 结构化输出端口

runtime MUST 通过显式输出端口发布其观察事件，MUST NOT 直接依赖终端渲染实现（Rich、全局 spinner、`sys.stdout`）。端口 MUST 至少覆盖：assistant 文本与思考展示、工具调用状态（开始/结果/错误）、预算更新、错误、子 Agent 归属与 run 生命周期事件。每个事件 MUST 携带足以关联的身份：`session_id`、`run_id`、以及适用时的 `attempt_id`、`tool_call_id`、`stream` 标识。端口 MUST NOT 接收或透出密钥、完整配置或不受限的内部状态。

#### Scenario: 无终端时 runtime 仍可运行

- **WHEN** 以仅记录事件的端口（headless）驱动一次 run
- **THEN** run 正常完成，且进程 stdout/stderr 不出现业务输出

#### Scenario: 事件携带可关联身份

- **WHEN** 端口收到文本、工具状态与预算事件
- **THEN** 每个事件都带 `session_id` 与 `run_id`，工具事件另带 `tool_call_id`，且同一 run 的事件可被归组

#### Scenario: 端口不接收敏感内容

- **WHEN** runtime 发布任意端口事件
- **THEN** 事件载荷不含 API 密钥、完整配置对象或未受控的内部字段

### Requirement: 输出端口是观察接口

端口 MUST 只用于观察：canonical 持久化 MUST 继续由既有 emitter/store 承担，MUST NOT 由端口实现决定。端口实现抛错 MUST NOT 改变 run 的终态、MUST NOT 改写 provider 消息或 canonical 事实；错误 MUST 被降级为诊断信息。

#### Scenario: 端口抛错不影响终态

- **WHEN** 端口实现在处理事件时抛出异常
- **THEN** run 仍按其自身逻辑到达唯一终态，canonical 事件内容与端口未抛错时一致

#### Scenario: 端口缺失时使用安全的默认实现

- **WHEN** 调用方未提供输出端口
- **THEN** runtime 使用一个不写业务输出的默认实现继续运行，而不是失败或回退到隐式终端打印
