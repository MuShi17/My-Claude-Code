## Why

代码侧 11 个原始 change 已全部 checked，但需求批次源文档仍为待确认/待开始；现有 acceptance report 还声称无 blocker，与两份独立审计冲突。没有一致的 artifact 状态就不能收口。

## What Changes

- 回写批次总览、任务卡和 Item 01-11 的实际 status、checkbox 和 evidence。
- 更新验收报告，区分已通过项、阻塞项和未覆盖风险。
- 在归档前重新执行 OpenSpec strict validation 和完整测试命令。

## Impact

影响 Knowledge Base 批次源文档、`docs/architecture` 验收报告、测试矩阵和 11 个修复 change 的完成状态；不改变历史代码事实。
