## 1. Parity 场景与比较器

- [x] 1.1 复用 C02 fixtures 建立双 Anthropic/OpenAI、权限、工具成功/失败、子 Agent、retry/cancel/terminal、partial 和 budget scenario catalog，并验证每项映射到 C01/C06/C07 契约
- [x] 1.2 实现 stable semantic comparator、允许差异分类和 before/after diff report，并用故意缺失 event/tool pairing/ref 的 golden 验证 blocker 不被掩盖
- [x] 1.3 生成 session/model/trace/usage/error/artifact/recovery 的 parity evidence，验证比较不依赖非确定时间、随机 ID 或真实 provider wire body

## 2. 故障与 CLI 验收

- [x] 2.1 运行 canonical commit/lock/corruption、legacy sink、provider、permission、dispatch/outcome、artifact/capture、projection/recovery 和 cancellation fault matrix，并验证每个边界有预期 terminal/diagnostic
- [x] 2.2 在临时 HOME/工作目录运行真实 CLI list/latest/resume/one-shot smoke，覆盖 canonical、legacy-only、stale/corrupt 和 rollback 场景，并验证不访问用户数据或网络
- [x] 2.3 运行 strict OpenSpec validation、完整离线测试、diff/未跟踪文件检查，并保存命令和结果作为 final Gate 证据

## 3. Authority 与 Gap Closure

- [x] 3.1 实现 legacy/shadow/canonical authority flag 和 blocker gate，验证存在未闭合 blocker 时切换被拒绝
- [x] 3.2 对每个 mismatch 建立 blocker/allowed/remaining gap 记录、owner、关联 task/test 和复跑结果，并验证闭合不依赖删除 golden 或用户数据
- [x] 3.3 在临时数据集执行切换后 parity、canonical resume 和 rollback，验证 runtime.sqlite、legacy logs/traces/llm/session/artifacts 均保留且可读
- [x] 3.4 输出最终 acceptance report，明确仍需用户批准的 authority 变更；验证本 change 未执行 commit、push、merge、release 或 destructive cleanup
