## Why

工具结果、模型请求和压缩摘要可能远大于上下文或单行日志限制；直接把它们塞进 canonical event 会导致 replay 失控、隐私暴露和 SQLite 膨胀。需要在不改变 canonical 事实的前提下，把大内容原子归档、用可验证 ref 替代，并提供明确的 LLM capture 模式。

## What Changes

- 为 compaction checkpoint 记录 source high-water、digest、schema/version、覆盖范围、摘要和 recent tail。
- 对大工具结果执行先归档后 placeholder 的原子流程，保存 hash、size、MIME、ref 和 redaction metadata。
- 设计 LLM capture off/metadata/redacted 三种策略，记录 request shape hash、usage/latency 和安全大小上限。
- 使 projection/replay 能识别 refs 并保持 bounded；归档失败时不写悬空 placeholder。
- 保证 compaction、archive 和 capture 均为 canonical 的派生/辅助数据，不修改既有 event。

## Capabilities

### New Capabilities

- `runtime-compaction-artifacts`: bounded compaction checkpoints、大结果 artifact archive 和隐私安全的 LLM capture 策略。

### Modified Capabilities

<!-- No existing capability is modified. -->

## Impact

- 影响 `src/mini_claude/agent.py` 的大结果路径、C05 `llm_captures`、C08 projection/replay、session compaction 和本地 artifact 目录。
- 可能新增 `~/.mini-claude/artifacts/` 文件；旧 tool-results 目录继续只读兼容。
- 依赖 C01、C04、C05、C08；不增加第三方依赖，不改 canonical event history。
