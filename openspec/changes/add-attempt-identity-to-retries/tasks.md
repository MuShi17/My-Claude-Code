## 1. Implementation

- [x] 1.1 为 ModelCallRecorder 增加 attempt_id 并贯穿 start/finish/error/retry
- [x] 1.2 将 attempt_id 写入 capture metadata 和相关 event refs/actions

## 2. Verification

- [x] 2.1 增加 retry attempt identity、重复 retry 和跨 provider fixture
- [x] 2.2 运行定向生命周期与 projection 测试，确认 identity 可查询关联
