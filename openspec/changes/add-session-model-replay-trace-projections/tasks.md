## 1. Projection 基础

- [x] 1.1 实现按 ordinal/high-water 的只读 event iterator、projection version/digest 和 decode/unmatched diagnostics，并用 C02 prefix fixture 验证重复 rebuild 稳定
- [x] 1.2 实现共享 identity/visibility/partial/tool pairing reducer，并用并行工具、child run、partial-only、thinking signature 和错误 fixture 验证

## 2. 三类投影

- [x] 2.1 实现 SessionProjection，重建 user/model/tool/error/terminal conversation state，并用 golden session 验证不读取 legacy 文件
- [x] 2.2 实现 ModelReplayProjection，生成 provider-neutral messages、call/result 配对、hidden filtering 和 bounded partial 规则，并用双 provider golden 验证
- [x] 2.3 实现 RunTraceProjection，输出 phase/identity/ordinal/latency/error/child/terminal 关系，并用 read-only test 验证不改变 canonical digest 或执行计数

## 3. 接入与 Gate

- [x] 3.1 将 Agent 下一轮 context、session 读取和 trace 查询改为 canonical-first，保留 C03 legacy 只读 fallback，并用关闭 legacy 文件的测试验证
- [x] 3.2 添加 prefix/high-water 前后 digest、provider parity、corruption、unmatched call/result 和 projection version tests，并运行 C02 suite
- [x] 3.3 运行离线回归与 strict OpenSpec validation，确认 C08 未反向修改 C01 event semantics 或提前引入 compaction/recovery authority
