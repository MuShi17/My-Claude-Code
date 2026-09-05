## 1. Checkpoint 与 archive

- [x] 1.1 实现 compaction checkpoint 的 source high-water/digest/schema/projection version、coverage、summary 和 recent tail，并用 C08 prefix fixture 验证重建一致与覆盖边界
- [x] 1.2 实现脱敏后 content-addressed artifact archive 的 temp write、flush/fsync、atomic rename、metadata 和 ref 校验，并用大结果/重复内容 fixture 验证
- [x] 1.3 将大工具结果路径改为先 archive 后 placeholder/ref，归档失败不发悬空 ref，并用 write/fync/metadata fault fixture 验证

## 2. LLM capture 策略

- [x] 2.1 实现 off/metadata-only/redacted 配置、invocation/attempt 关联、request shape hash、usage/latency 和 policy version，并用三模式 golden 验证
- [x] 2.2 实现 capture body size bound、redaction、可选 archive/ref 和 integrity metadata，并用 secret/oversize fixture 验证无原值无限落盘
- [x] 2.3 让 projection/session/diagnostic 只按权限和 bounded policy 展开 refs，并验证辅助数据读取不触碰 canonical append/ordinal/seal

## 3. Recovery 与 Gate

- [x] 3.1 添加 orphan archive、悬空 ref、hash mismatch、checkpoint source mismatch 的诊断/修复入口，并用重开/fault fixture 验证不改 canonical history
- [x] 3.2 保留旧 `tool-results` 只读兼容并验证新旧 archive 不互相覆盖，运行 C02 compaction/privacy suite
- [x] 3.3 运行离线回归与 strict OpenSpec validation，确认 C09 不引入远程存储、自动清理或 canonical rewrite
