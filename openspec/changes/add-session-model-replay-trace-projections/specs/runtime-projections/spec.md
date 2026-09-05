## Purpose

将 immutable canonical runtime events 确定性地重建为用户 session、下一轮 provider messages 和诊断 trace，使读取、模型输入、恢复和 parity 都共享同一 high-water 事实前缀。

## ADDED Requirements

### Requirement: Session projection rebuilds conversation state

Session projection MUST 按 session/turn/run identity 和 ordinal 重建用户消息、模型文本、tool call/result、错误、终态及必要 metadata；重建结果 MUST 不依赖 legacy 日志文件。

#### Scenario: Rebuild from an immutable prefix

- **WHEN** 给定 session 的 canonical event prefix 和 high-water
- **THEN** projection 产生确定性的 conversation state，并可在相同 prefix 上重复得到同一 digest

#### Scenario: Child run is included without flattening identity

- **WHEN** session 包含 parent 和 child run
- **THEN** session projection 能显示 child 的关联结果，同时保留 run/parent identity，不把 child tool call 当成 parent call

### Requirement: Model replay produces provider-valid messages

Model replay MUST 根据 canonical semantic events 生成可供 Anthropic/OpenAI adapter 使用的 provider-neutral message sequence，并正确处理 tool call/result 配对、thinking signature、model visibility、partial 和 hidden events。

#### Scenario: Completed tool call is replayed

- **WHEN** prefix 包含完整 function call、tool outcome 和 function response
- **THEN** replay message 中 call/result 关联稳定且顺序满足目标 provider 的输入约束

#### Scenario: Partial or hidden event is encountered

- **WHEN** prefix 仅包含 partial arguments、internal diagnostic 或对模型不可见事件
- **THEN** replay 不把未完成调用作为可执行最终消息，也不把 hidden diagnostic 注入模型上下文

### Requirement: Trace projection is diagnostic-only

Run trace MUST 展示 phase、model call、permission、dispatch、tool outcome、error、retry、child 和 terminal 的时间/ordinal/identity 关系；trace 读取或重建不得改变模型输入、工具执行或 canonical facts。

#### Scenario: Trace shows failed tool

- **WHEN** run 包含 permission、dispatch、工具失败和 terminal failed
- **THEN** trace 能按稳定 identity/ordinal 展示完整因果链，并标记失败类别

#### Scenario: Trace rebuild is read-only

- **WHEN** 用户请求从同一 high-water 重建 trace
- **THEN** canonical event count/digest、model replay 和工具执行状态均不改变

### Requirement: Projections expose high-water and diagnostics

每个 projection MUST 返回输入 high-water、schema/projection version、stable digest 和可定位的 unmatched/invalid/unsupported event diagnostics；不能静默跳过影响语义的事件。

#### Scenario: Rebuild after append

- **WHEN** 在 high-water H 后追加新事件并以 H 重新读取
- **THEN** projection digest 与原 H 相同；以新 high-water 读取才包含新增事件

#### Scenario: Call/result mismatch

- **WHEN** projection 发现没有对应 function call 的 tool result 或相反
- **THEN** 返回明确诊断并按策略生成 bounded placeholder/不完整状态，不能伪造完整配对

### Requirement: Projection behavior is provider-neutral and parity-comparable

逻辑等价的 Anthropic/OpenAI canonical event 序列 MUST 产生等价的稳定 session/model/trace 字段；provider-specific 表示只在 adapter 边界存在。

#### Scenario: Equivalent provider runs are compared

- **WHEN** 双 provider fixtures 表达相同用户 turn、模型意图和工具结果
- **THEN** stable projection comparator 报告等价，允许差异仅限显式 provider metadata

#### Scenario: Projection version changes

- **WHEN** projection 算法升级但 canonical schema 不变
- **THEN** version/digest 变化可见且旧 canonical events 仍可按兼容版本重建，不能改写历史事件
