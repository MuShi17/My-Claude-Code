## 1. Implementation

- [x] 1.1 在真实 compaction 入口构建并写入 high-water/digest checkpoint
- [x] 1.2 将大结果归档与 bounded ref 接入 Agent compaction/context 路径

## 2. Verification

- [x] 2.1 增加真实 Agent compaction、重启重建和追加 immutable prefix 测试
- [x] 2.2 注入 archive/checkpoint 失败，确认无悬空 ref 且保留 canonical prefix
