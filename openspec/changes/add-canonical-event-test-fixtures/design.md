## Context

C01 已定义 event envelope、ordinal/high-water、partial/terminal、redaction 和 provider-neutral 边界。当前仓库已有 Python 包但没有可供本批次复用的 `tests/` 目录，因此测试布局需要与现有 `src/pyproject.toml` 的测试入口一起确认；不能把真实 API 当作核心验证依赖。

## Goals / Non-Goals

**Goals:**

- 用一个确定性 scenario builder 驱动两个 provider、内存/SQLite sink 和各 projection。
- 通过 golden 与 stable comparator 让差异报告可审查。
- 用 fault hooks 覆盖 crash/reopen/duplicate/partial/terminal 边界，并为 C03～C11 预留合约测试。

**Non-Goals:**

- 不测试真实 provider 的网络稳定性、吞吐或供应商 wire 协议。
- 不在夹具中复制生产逻辑，不用测试专用分支绕过契约。
- 不把非确定性的绝对时间、随机 UUID、临时路径或本地环境纳入 parity digest。

## Decisions

1. **Scenario-first builder。** 用固定 `Scenario` 描述 user turn、model chunks、tool calls、permission、结果、cancel/fault 点，由适配器分别映射到 provider 和 sink；比每个测试手写事件序列更能保持跨 change 一致。
2. **Stable projection comparator。** 比较 identity/kind/status/ordinal 关系、文本和 refs 等稳定字段；时间只验证格式/相对顺序，随机值通过固定 factory 注入。直接比较全 JSON 会把无关差异误判为回归。
3. **Temporary SQLite per test。** 每个故障场景使用独立临时目录和显式连接关闭/重开，避免共享进程状态掩盖 durability 问题。
4. **Golden 只存脱敏最小载荷。** golden 用于语义回归，不存完整 provider request 或任意大工具结果；artifact 测试单独验证 hash/size/ref/placeholder。
5. **先 contract 后实现。** C02 的失败测试先固定契约；后续 change 只能补实现或 fixture，不得为了让测试通过削弱 C01 断言。

## Risks / Trade-offs

- [夹具过度简化真实循环] → 同时覆盖流式 chunk、并行工具、错误、取消、partial-only 和双 provider lifecycle，并保留 CLI smoke。
- [golden 版本漂移] → golden 顶层带 schema version，更新必须伴随明确 diff 和 parity 说明。
- [fault hook 污染生产代码] → hook 通过可选测试注入协议进入，不在默认路径改变执行语义。
- [当前无 tests 目录] → C02 任务先以现有打包/测试命令探测为验收条件，再固定测试根目录。

## Migration Plan

先创建离线 contract suite 与 builders，再由 C03～C10 逐步使其转绿；C11 复用同一场景集合进行 shadow parity、回滚和 CLI smoke。测试资产不读取或修改用户的真实 `~/.mini-claude` 数据。
