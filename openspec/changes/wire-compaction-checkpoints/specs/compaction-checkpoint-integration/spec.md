## Purpose

让真实 compaction 产生可验证、可恢复的 canonical checkpoint。

## ADDED Requirements

### Requirement: Real compaction writes a checkpoint

真实 Agent compaction MUST 记录 source high-water、digest、coverage、tail 和 projection version。

#### Scenario: Context compacts after a turn

- **WHEN** Agent 达到 compaction threshold
- **THEN** checkpoint 写入当前 canonical prefix，并可从相同 prefix 验证和重建

### Requirement: Archive precedes bounded references

大 payload MUST 先成功归档并校验 hash，再进入 bounded event/ref；归档失败不得产生悬空 ref。

#### Scenario: Artifact fsync fails

- **WHEN** artifact fsync 或 metadata commit 失败
- **THEN** canonical flow 保留原始 prefix，返回可诊断失败，不写不存在对象的 ref
