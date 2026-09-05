## Why

retry 事件当前只有 attempt number，没有稳定 `attempt_id`，不能可靠关联 model start、retry、finish/error 和 usage。

## What Changes

- 为每次 provider attempt 生成稳定 identity。
- 将 identity 贯穿 recorder、retry event、capture metadata 和 projection。
- 增加跨 retry 的关联测试。

## Impact

影响 `runtime_lifecycle.py`、`agent.py`、capture metadata、fixtures 和 projection tests。
