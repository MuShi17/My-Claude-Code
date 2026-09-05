## Why

这次重构同时涉及 provider 生命周期、工具副作用、SQLite 顺序、投影和恢复；没有固定 ID、时间、响应和故障点的测试夹具，就无法区分真实回归与非确定性差异，也无法安全比较 legacy shadow 与 canonical projection。

## What Changes

- 建立覆盖 envelope、store、provider lifecycle、durable tool boundary、子 Agent、projection、recovery、compaction 和隐私的测试矩阵。
- 提供不依赖网络/API key 的固定时间、ID、Provider 响应、工具结果和权限决策 fixture builders。
- 增加 golden canonical events/messages/traces 与稳定字段 comparator。
- 增加临时 SQLite、故障注入、CLI `--resume` smoke 和双 provider contract 测试入口。
- 要求先写可失败的契约测试，再由 C03～C10 实现满足它们的行为。

## Capabilities

### New Capabilities

- `canonical-event-test-fixtures`: 可复现的测试矩阵、事件夹具、golden 快照、故障注入和 parity 比较能力。

### Modified Capabilities

<!-- No existing OpenSpec capability is modified. -->

## Impact

- 影响 `src/` 下 Python 测试布局、未来 runtime event/store/projection 接口和 CI 测试命令。
- 只使用标准库与项目已有测试依赖，不需要真实 provider 网络访问。
- 依赖 C01 的冻结契约；为 C03～C11 提供可复用的验证资产。
