## Decisions

1. provider loop 每次下一轮请求前从当前 canonical high-water 读取 `ModelReplayProjection`。
2. provider-specific message encoding 只发生在 adapter 边界。
3. 若 canonical replay 失败，按照 authority 进入可观测 controlled failure，不偷偷从陈旧 legacy array 继续 canonical 运行。
4. shadow 模式保留 legacy context 做 comparator，但 canonical projection 负责报告差异。
