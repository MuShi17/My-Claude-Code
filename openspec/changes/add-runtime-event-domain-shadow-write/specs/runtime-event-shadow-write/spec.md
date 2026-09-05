## Purpose

提供一个可校验、可脱敏、可组合的 RuntimeEvent 发射边界，让模型、工具和生命周期事件以统一语义同时写入 canonical 与 legacy shadow，而不把存储实现耦合到 Agent Loop。

## ADDED Requirements

### Requirement: Runtime events are validated before emission

发射门面 MUST 接受符合 C01 envelope 的 provider-neutral event，并在写入任一 sink 前校验 schema、identity、载荷类型、visibility、refs 和 partial/terminal 约束；无效事件 MUST 返回明确错误。

#### Scenario: Valid event reaches a recording sink

- **WHEN** 调用方提交合法 text、tool 或 lifecycle event
- **THEN** recording sink 收到完整 canonical event，且字段未被静默改名或丢弃

#### Scenario: Invalid event is rejected consistently

- **WHEN** 调用方提交缺失 run identity、冲突 terminal 或非法 content/action 组合
- **THEN** 门面拒绝写入并让调用方可区分契约错误与 sink I/O 错误

### Requirement: Identity and canonical encoding are centralized

所有事件 MUST 由统一的 ID/RunContext 组件生成或校验，canonical serialization MUST 稳定且可计算 digest；调用方不得为 Anthropic、OpenAI、tool 或 child run 各自拼接另一套身份字段。

#### Scenario: Two providers use the same context shape

- **WHEN** 两个 provider 发射逻辑等价的 model event
- **THEN** 它们使用相同的 session/turn/run/invocation 坐标结构，仅 provider metadata 有受控差异

#### Scenario: Serialization order changes

- **WHEN** 同一 RuntimeEvent 的输入 dict 顺序不同
- **THEN** canonical bytes 和稳定 digest 相同

### Requirement: Redaction and bounded references are applied at the sink boundary

在事件离开进程前，门面 MUST 按统一 policy 脱敏凭据、token、环境值和受保护参数；大载荷 MUST 能转成有 hash/size/MIME/ref 的 bounded placeholder，且 redaction version 可追踪。

#### Scenario: Secret is emitted

- **WHEN** tool args 或 provider metadata 包含凭据样式值
- **THEN** canonical 和 legacy shadow 接收到的内容均已脱敏，并保留必要的字段路径/策略版本 metadata

#### Scenario: Large result is referenced

- **WHEN** 工具结果超过当前 inline 限制
- **THEN** 事件只携带 bounded placeholder 和 artifact reference，不阻塞后续 event ordering

### Requirement: Composite sink has explicit failure policy

组合 sink MUST 区分 canonical sink 与 diagnostic legacy sink 的失败：canonical 写入失败 MUST 反馈给 runtime 并阻止声称事实已持久化；legacy shadow 失败 MUST 记录诊断并按配置继续，不得反向改变模型或工具执行结果。

#### Scenario: Canonical sink fails

- **WHEN** canonical sink 在 emit 时返回不可恢复 I/O 错误
- **THEN** 门面向调用方返回失败，调用方不能继续将该 event 当作已 durable 的事实

#### Scenario: Legacy sink fails

- **WHEN** legacy sink 写入失败但 canonical sink 已成功
- **THEN** canonical event 仍可读取，诊断报告包含失败，模型/工具主流程按 policy 继续

### Requirement: Shadow mapping preserves old log compatibility

legacy adapter MUST 将可映射 RuntimeEvent 写入既有 JSONL/trace/LLM 形状并保留 session/ask 关联；不可映射字段 MUST 以兼容的 metadata/ref 表达，不得伪造旧格式中不存在的副作用。

#### Scenario: Model and tool events are shadowed

- **WHEN** canonical 发射一次模型调用和一个工具 outcome
- **THEN** 旧日志中出现可由现有 reader 读取的对应记录，并可回指 canonical identity

#### Scenario: Unmapped lifecycle event is shadowed

- **WHEN** 事件没有旧格式的一对一字段
- **THEN** adapter 保留可诊断的兼容记录或明确 gap，不丢失 canonical 事件也不制造虚假的工具 dispatch
