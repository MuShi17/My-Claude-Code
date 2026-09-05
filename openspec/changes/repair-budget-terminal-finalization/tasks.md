## 1. Implementation

- [x] 1.1 修复 Anthropic/OpenAI budget 路径，避免对已 finish 的 recorder 重复收尾
- [x] 1.2 统一 budget terminal 与 RunStateGuard 的唯一封存和 late-event 行为

## 2. Verification

- [x] 2.1 增加 max cost/max turns、provider error、取消和重复 terminal 场景
- [x] 2.2 使用 py313 运行生命周期 fault matrix，确认每个 budget run 有唯一 terminal
