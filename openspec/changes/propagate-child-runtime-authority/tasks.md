## 1. Implementation

- [x] 1.1 抽取并传递 child runtime authority/approval/rollback/capture policy 配置
- [x] 1.2 保持 parent/child store/sink 和 run identity 关联，记录显式覆盖来源

## 2. Verification

- [x] 2.1 增加 skill-fork、sub-agent、nested child 的 policy inheritance 测试
- [x] 2.2 验证 child retry/cancel/rollback 不绕过父级 gate
