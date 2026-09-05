## 1. 测试布局与矩阵

- [x] 1.1 根据 `src/pyproject.toml` 现状确定测试根目录、标记和最小离线命令，并验证空环境下命令发现失败不会被静默跳过
- [x] 1.2 将 envelope/store、provider lifecycle、tool boundary、child/terminal、projection/recovery、compaction/privacy 场景登记成矩阵，并验证每个 C03～C11 责任有对应条目

## 2. 确定性夹具

- [x] 2.1 实现固定 clock、ID factory、logical run context、permission decision 和环境隔离 builder，并用两次运行相同 digest 的测试验证
- [x] 2.2 实现 Anthropic/OpenAI fake provider 的等价响应脚本、工具结果和流式 chunk fixture，并验证不读取网络或 API key
- [x] 2.3 创建脱敏 golden event/message/trace/compaction 样本和 stable-field comparator，并验证随机 UUID、绝对时间、临时路径不会影响比较结果

## 3. 契约与故障测试

- [x] 3.1 先添加 C01 envelope、顺序、partial、terminal、redaction 和 idempotency 的可失败 contract tests，并验证测试在能力缺失时返回明确失败
- [x] 3.2 添加临时 SQLite 重开、重复 append、写失败、损坏记录和 fault hook 场景，并验证每个场景能断言预期 recovery 状态
- [x] 3.3 添加双 provider、子 Agent、projection/replay、`--resume` 和 legacy shadow smoke 入口，并验证 suite 可在无真实 API 的环境运行

## 4. 交付 Gate

- [x] 4.1 为测试资产编写运行说明和网络/API key 约束，并验证 CI/本地命令覆盖纯契约子集与完整 suite
- [x] 4.2 运行 OpenSpec strict validation，确认 spec 每个 requirement 都有四级 Scenario 且 tasks 全部包含验证方式
