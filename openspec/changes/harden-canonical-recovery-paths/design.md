## Decisions

1. 使用 `~/.mini-claude/sessions/{session_id}/runtime.sqlite` 作为 session runtime store 的规范路径。
2. store 打开、schema、integrity 或 partial corruption 异常必须返回明确 recovery classification。
3. legacy fallback 仅允许在明确的 legacy-only/missing 场景，不能在 corruption 场景触发。
4. 恢复过程只读诊断并保留原文件；受控 closure 另有显式操作。
