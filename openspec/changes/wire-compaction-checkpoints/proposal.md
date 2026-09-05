## Why

Compaction 模块和 `CompactionCheckpointBuilder` 已存在，但真实 Agent compaction 仍直接调用 summarizer 并修改内存消息，没有留下 high-water/digest checkpoint。

## What Changes

- 在真实 compaction 入口写 checkpoint。
- 先归档大结果再发 bounded ref。
- 保留 immutable canonical prefix，支持重启重建和失败诊断。

## Impact

影响 `agent.py`、`compaction.py`、artifact/store integration 和 compaction tests；不改写历史 canonical event。
