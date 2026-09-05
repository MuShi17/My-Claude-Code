## Why

现有 `--resume` 主要依赖 session.json 的消息快照，无法可靠识别 runtime store 中 open、partial-only、terminal、unmatched 或损坏的运行；直接把旧日志拼成新 dispatch 还可能重复执行副作用。需要让恢复从 canonical high-water 和 terminal facts 出发，并把 legacy 仅作为安全的只读兼容来源。

## What Changes

- 增加 RecoveryProjection，扫描 terminal/open/partial-only/unmatched/corrupt/uncertain 状态并输出可操作诊断。
- 启动时对可判定的 open run 以幂等方式追加 failed/cancelled/aborted 等终态，禁止猜测成功或自动重放工具。
- 将 session.json 升级为 canonical-derived v2 snapshot，并保存 source high-water/digest/version。
- 让 `Agent.restore_session`、CLI list/latest/resume canonical-first；旧 session/log/traces/llm 只读 fallback，不虚构 dispatch。
- 提供旧数据安全读取/迁移边界，不删除或原地改写 legacy 与 canonical 数据。

## Capabilities

### New Capabilities

- `canonical-resume`: 基于 canonical event store/projection 的恢复、session v2、CLI resume 和 legacy 只读兼容。

### Modified Capabilities

<!-- No existing capability is modified. -->

## Impact

- 影响 `src/mini_claude/session.py`、`agent.py`、`__main__.py`、recovery projection 和 session 文件格式。
- 依赖 C05、C07、C08、C09；会读取旧 JSONL/session/traces/llm，但不把它们升级为 canonical authority。
- 公开 `--resume`/session list 行为保持兼容，并新增明确的 open/corrupt/legacy diagnostics。
