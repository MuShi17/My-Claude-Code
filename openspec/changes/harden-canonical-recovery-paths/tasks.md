## 1. Implementation

- [x] 1.1 统一 session runtime.sqlite 路径并更新 CLI/session/store 调用点
- [x] 1.2 为 canonical open/recovery 增加 corruption-preserving classification，禁止静默 legacy fallback

## 2. Verification

- [x] 2.1 增加 missing/corrupt/partial/schema mismatch/legacy-only 的 CLI 与 recovery 测试
- [x] 2.2 验证 resume 幂等、原始数据库保留、路径隔离和诊断输出
