## Purpose

控制长工具结果、压缩上下文和模型捕获数据的大小与隐私风险，同时保留可验证的来源、覆盖范围和 artifact 引用，让 canonical replay 始终有界且不被辅助存储反向改写。

## ADDED Requirements

### Requirement: Compaction checkpoints identify their source

每个 compaction checkpoint MUST 记录 source high-water、source digest、canonical schema version、compaction/projection version、摘要覆盖范围、recent tail 和创建时间；checkpoint 不得声称覆盖未读取的事件。

#### Scenario: Checkpoint is rebuilt from a prefix

- **WHEN** 对指定 high-water H 生成 compaction checkpoint
- **THEN** checkpoint 能校验 source digest/coverage，并在同一 prefix 上重建得到一致结果

#### Scenario: Source changes after checkpoint

- **WHEN** H 之后追加 canonical events
- **THEN** 旧 checkpoint 仍标记 H 的覆盖范围，新事件不会被静默归入旧摘要

### Requirement: Large results are archived before placeholder emission

超过 inline limit 的 tool/model payload MUST 先以原子方式写入 artifact archive，再在 canonical/reference event 中写 bounded placeholder；placeholder MUST 含 hash、size、MIME、ref、redaction/version metadata，归档失败时不得产生悬空 ref。

#### Scenario: Large tool result is available

- **WHEN** 工具返回超过 inline 上限的结果
- **THEN** archive 先成功持久化并可校验 hash，随后 event 携带 bounded placeholder/ref，projection 不展开完整内容

#### Scenario: Archive write fails

- **WHEN** artifact 文件写入、fsync 或元数据提交失败
- **THEN** canonical flow 返回可诊断 archive error 或保留 bounded failure outcome，不写指向不存在对象的 ref

### Requirement: Artifact references are content-addressed and bounded

artifact MUST 使用稳定内容 hash 或等价完整性标识、字节 size、MIME/encoding、创建者 scope 和 redaction policy；读取/展开必须受大小、权限和调用方请求限制。

#### Scenario: Artifact is reopened

- **WHEN** projection 或诊断请求一个已存在 ref
- **THEN** 系统校验 hash/size/权限后返回限定内容或 bounded preview，篡改时返回 integrity error

#### Scenario: Duplicate content is archived

- **WHEN** 两次归档产生相同脱敏内容
- **THEN** ref/digest 可复用或明确去重，不能因为重复归档改变 canonical event ordering

### Requirement: LLM capture obeys explicit privacy modes

LLM capture MUST 支持 off、metadata-only 和 redacted 三种模式；每条 capture 至少能关联 invocation/attempt，metadata mode 记录 provider/model、request shape hash、usage、latency 和 size，redacted mode 还保存经 policy 脱敏的 bounded body。

#### Scenario: Capture is off

- **WHEN** 用户选择 off 模式
- **THEN** 不持久化 request/response body，仅保留满足运行诊断所需的最小 event metadata，且模型执行不受影响

#### Scenario: Redacted capture exceeds bound

- **WHEN** redacted request/response 超过配置上限
- **THEN** 系统按明确策略截断或归档并保留 size/hash/ref，不写入无限增长的 capture row

### Requirement: Auxiliary data never mutates canonical history

compaction、artifact archive 和 LLM capture MUST 通过 refs/high-water 与 canonical event 关联；失败、清理或重建辅助数据不得改写、重排、删除已提交 canonical events。

#### Scenario: Rebuild compaction

- **WHEN** 删除并重建某个 projection/compaction artifact
- **THEN** canonical event count、ordinal、digest 和 terminal seal 保持不变

#### Scenario: Privacy policy changes

- **WHEN** redaction policy version 升级
- **THEN** 新 capture 使用新版本并可区分，历史 canonical event 不被原地解密或重写
