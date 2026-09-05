## Why

Canonical store 打开/恢复异常时，CLI 将 store 置空并继续 legacy fallback；同时 Item 05 与实际 runtime.sqlite 路径不一致。损坏可能被误判为无 canonical 数据。

## What Changes

- 统一按 session 隔离的 runtime store 路径。
- 将 missing、corrupt、partial-tail、schema mismatch 和 legacy-only 分类化。
- Canonical corruption 保留数据库和诊断，禁止静默 fallback。

## Impact

影响 `__main__.py`、session/recovery、路径常量、CLI tests 和需求文档同步点。
