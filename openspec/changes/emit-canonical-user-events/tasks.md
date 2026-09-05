## 1. Implementation

- [x] 1.1 在 Anthropic/OpenAI 真实 loop 发射 canonical user event 并定义注入上下文语义
- [x] 1.2 将用户事件与 turn/run/invocation identity 关联并保持 shadow parity 可比较

## 2. Verification

- [x] 2.1 增加双 provider fake one-shot 的 user/model/tool/terminal 事件链测试
- [x] 2.2 验证 Session/Model Replay/Recovery 从 canonical stream 重建原始用户输入
