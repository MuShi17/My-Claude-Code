## Why

当前 Mini Claude Code 会在 Provider 首次请求前把大工具结果替换为 bounded_ref 元数据，但 Provider 和子 Agent 没有可消费该引用的 ArchiveRead 能力，因此模型只能看到 ref、size 和 hash，无法获得文件正文或继续读取。Maka 的流程是最新结果首次请求优先保留完整正文，只有容量确实不足或结果变 stale 时才使用带读取指引的归档占位符；本变更补齐这一闭环。

## What Changes

- 新增受 session/lineage scope、hash、size 和 response cap 保护的 runtime ArchiveRead capability，支持归档结果的 inspect、bounded read 和必要的 query。
- 修改工具结果 Provider 投影：首次请求容量允许时保留完整安全正文；首次请求放不下时返回前 N 字符预览、truncated、ref、next_offset 和可实际调用的 ArchiveRead 指引。
- 修改 stale 结果投影：使用可行动的 bounded placeholder，不再把裸 bounded_ref 作为模型唯一结果。
- 让父 Agent 和子 Agent 从同一 runtime capability 派生 ArchiveRead，保持普通 child tool allowlist 不扩大。
- 分离 canonical runtime result、Provider projection 和 terminal projection；终端显示有界正文/状态/读取提示，不把 metadata 冒充文件内容。
- 保持既有 redaction、artifact ref 兼容、fail-closed 和 store 生命周期约束；ArchiveRead 失败、scope 不匹配、hash 不匹配和 store closed 均返回结构化错误。
- 增加双 Provider、容量、stale、ArchiveRead 分页、父子 Agent、终端和生命周期回归测试。

## Capabilities

### New Capabilities

- \`tool-result-archive-read-projection\`: 定义大工具结果的归档读取能力、首次 Provider 请求投影、容量救援预览、stale 占位符、父子 Agent scope 和终端投影边界。

### Modified Capabilities

无。当前 openspec/specs/ 没有已登记的主规格；本变更将新能力的行为合同集中在对应 delta spec 中。

## Impact

- 影响 src/mini_claude/artifact_archive.py、runtime_lifecycle.py、provider_content.py、projections/、agent.py、subagent.py、tools.py、ui.py 及其测试。
- 影响 Anthropic 和 OpenAI-compatible Provider 的工具结果消息内容，但不改变工具执行和副作用调度协议。
- 需要复用现有 artifact store、Canonical Runtime Event、redaction 和模型 replay；不新增外部依赖，不迁移或删除既有 artifact。
- 参考 D:/workspace/maka@57e08d83497d1d7ace7d6eff88e4e5267a0345b5，仅复制行为语义，不修改参考仓库。

