## 1. Provider 生命周期统一

- [x] 1.1 抽取 Anthropic/OpenAI common model lifecycle 与 ModelCallRecorder，记录 invocation、attempt、stream/partial、final、usage、latency、finish/error，并用双 provider fake fixture 验证等价事件
- [x] 1.2 将 request shape hash、provider/model metadata 和 unknown usage 规则接入 recorder，并用 success/error/budget fixture 验证缺失字段不被伪造为零
- [x] 1.3 将 streaming text/tool chunks 转换为 bounded partial 与 final payload，并用分片 arguments/非法 JSON fixture 验证 final 前不执行工具

## 2. Durable tool boundary

- [x] 2.1 将 permission decision 与 function-call identity 接入统一事件路径，并用 allow/deny/unknown permission fixture 验证拒绝时无副作用 outcome
- [x] 2.2 在 tool runner 前追加并确认 durable `tool_dispatch`，在执行后追加 outcome/function response，并用 fault hook 验证 dispatch 失败时工具调用次数为零
- [x] 2.3 覆盖并行工具、工具异常、超时、取消、预算耗尽和 provider error，验证每个 call 有可配对的 terminal/error 状态

## 3. Source-of-truth 切换

- [x] 3.1 移除 Agent Loop 对 `AgentLogger.log_*` 返回/状态的事实依赖，保留仅由 facade 驱动的 legacy shadow，并用关闭 legacy sink 的测试验证执行语义不变
- [x] 3.2 接入 C05 store 的 high-water/flush 边界，验证模型下一轮消息只从 canonical projection 输入，不读取 legacy 文件补事实
- [x] 3.3 运行 C02 provider/tool contract suite、项目离线回归和 strict OpenSpec validation，确认 C06 未提前引入 continuation 或自动副作用重试
