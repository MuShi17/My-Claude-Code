## Decisions

1. compaction 以 canonical store high-water 为输入，checkpoint 记录 source digest、coverage、tail 和 projection version。
2. summarizer 只生成摘要内容，不拥有 canonical message history 的写权限。
3. 大 tool/model payload 先经 ArtifactArchive 原子写入，再把 ref 放入事件或 projection。
4. checkpoint/artifact 失败时保留 canonical prefix，并让当前运行进入可诊断状态。
