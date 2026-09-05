## Context

目标仓库当前实际基线为 `main@35a06324e68e2113b93a38b55ae4747030458cdd`，工作区目前 clean；批次文档中记录的 `331c84...` 与 dirty 状态已过时，Item 01 实施时必须重新取证。Maka 只读参考固定为 `main@1e543a7385614adc671623efe2586cf5317582d4`。现有 `AgentLogger` 按 ask/LLM 分文件写 JSONL，`Tracer` 将 turn 直接追加到文件，Agent Loop 在两个 provider 分支中直接调用多种日志方法。

## Goals / Non-Goals

**Goals:**

- 形成后续 change 可共同依赖的事件语义、身份和存储不变量。
- 使模型上下文、恢复、trace、compaction 和 parity 都能从同一 immutable event prefix 推导。
- 将 shadow、切换、回滚、隐私和旧数据保留规则变成可测试的边界。

**Non-Goals:**

- 不移植 Maka 的完整 Agent Graph、continuation、workspace authority 或复杂 tool journal。
- 不在本 change 实现 SQLite、Agent Loop 接入或 projection；这些分别由 C05～C10 完成。
- 不支持分布式多 writer、跨机器复制、自动重试不确定副作用或 bit-exact provider wire replay。

## Decisions

1. **以语义 envelope 为事实源。** 采用固定的 session/turn/run/invocation 坐标与 content/actions/refs 分类；原始 provider 响应只作为受控捕获，避免 provider 分支成为第二套事实模型。备选是直接保存 provider JSON，但它无法稳定支持双 provider replay。
2. **由持久化 writer 分配顺序。** 时间戳只用于观察，ordinal/high-water 才是读取和封存依据。备选是 UUID 或时间排序；两者都不能证明同一 writer 下的先后关系。
3. **终态是唯一封存权威。** terminal event 成功提交后形成 seal；partial 只表示增量，不产生终态。备选是依赖 session.json 的 status，但它会与实际事件落盘产生竞态。
4. **Canonical-first、legacy-shadow。** 第一阶段保留旧文件写入及只读兼容，且把诊断 sink 的失败策略显式化；不通过删除旧数据回滚。备选是一次性替换，会失去 parity 和旧会话保护。
5. **单进程/单逻辑 writer 作为本批次边界。** 如需跨进程并发，回到契约 change 扩展锁和一致性定义，而不是在 SQLite change 中隐式升级范围。

## Risks / Trade-offs

- [契约过早冻结] → 用 deterministic fixtures、strict validation 和 schema_version upgrade 路径验证；投影发现语义缺口时回到 C01/C04。
- [脱敏损失调试信息] → 保留 redaction version、字段路径、hash/size/ref 等可审计 metadata，并把原文捕获限定到显式策略。
- [双写增加延迟或失败面] → canonical sink 与 diagnostic sink 使用独立 failure policy；所有写入边界添加故障测试。
- [当前基线与任务文档不一致] → C01 首个实施任务记录 branch、HEAD、remote、diff 和唯一 writer，并以记录为后续验收输入。

## Migration Plan

先冻结并校验本契约，再实现内存 sink 与 SQLite store；Agent Loop 在 shadow 阶段同时保留旧格式。完成 projection、recovery 和 parity 后才允许配置切换 authority；回滚只改变路由开关，不删除 `runtime.sqlite`、JSONL、traces、llm 或 session 文件。
