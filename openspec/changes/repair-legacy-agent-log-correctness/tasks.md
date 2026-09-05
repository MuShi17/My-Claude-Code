## 1. Logger 生命周期与时间

- [x] 1.1 为 parent/child logger 增加显式 ask 生命周期和关联字段，修复无 active ask 时丢事件，并用 child success/failure fixture 验证所有已发生事件可读
- [x] 1.2 引入可注入 UTC clock 的真实毫秒 timestamp formatter，并用同秒非整秒事件和固定 clock 测试验证格式与毫秒保留
- [x] 1.3 补齐 api response 到 LLM JSONL 的 `llm_ref` 写入、解析和失败状态，并用 capture failure fixture 验证无悬挂引用

## 2. Tracer 与异常持久化

- [x] 2.1 调整 turn/tool 合并和持久化顺序，使落盘 trace 含工具调用、结果、错误和耗时，并用多工具 turn golden 验证关联
- [x] 2.2 在正常、异常、取消和 child 退出路径统一执行幂等 flush，并用故障 fixture 验证原始异常不被 flush 错误遮蔽
- [x] 2.3 保持旧目录、文件命名、reader 所需字段和公开 Agent API 不变，并用旧/新 legacy fixture 回归验证

## 3. Shadow 前 Gate

- [x] 3.1 运行 C02 legacy correctness contract suite，验证 child、timestamp、llm_ref、trace details 和 flush 全部有稳定证据
- [x] 3.2 在隔离 worktree 检查现有用户 diff 未被覆盖，并运行项目已有离线测试命令验证无公开行为回归
