## 1. Implementation

- [x] 1.1 让 off/metadata-only 策略阻止 legacy raw LLM 写入，并保留最小可诊断 metadata
- [x] 1.2 统一 shadow/canonical capture status 与失败处理，确保不产生悬空 llm_ref

## 2. Verification

- [x] 2.1 增加 Agent 入口的 off、metadata-only、redacted secret marker 集成测试
- [x] 2.2 使用 py313 运行相关测试并记录无 raw secret 的证据
