## Decisions

1. CLI smoke 使用临时 HOME 和 deterministic fake provider，不依赖网络或真实密钥。
2. one-shot、list/latest、resume、shadow、rollback 均从命令行入口验证。
3. stale xfail 转为正常断言；gate 不比较固定的历史 passed 文本，而比较退出码和结构化结果。
4. provider metadata、时间、随机 ID 等允许差异必须显式列入 comparator。
