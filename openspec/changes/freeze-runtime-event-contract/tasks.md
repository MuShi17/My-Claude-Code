## 1. 基线与参考证据

- [x] 1.1 记录目标仓库 branch、HEAD、remote、tracked/untracked diff、现有用户修改、选定 worktree 和唯一 writer，并验证记录与当前 `git status`/`git rev-parse` 输出一致
- [x] 1.2 固定 Maka 只读参考 commit 和实际阅读文件，记录采用与不采用的设计清单，并验证后续文档引用同一 commit

## 2. 契约冻结

- [x] 2.1 将 envelope 字段、身份层级、content/actions/refs、provider visibility 和 canonical 编码规则写入可审查的契约文档，并用 strict OpenSpec 校验验证每个 requirement 有场景
- [x] 2.2 明确 ordinal/high-water、partial、terminal seal、幂等重复写和非法后续写的状态转换，并为每个不变量登记将由 C02/C05 覆盖的测试入口
- [x] 2.3 明确 redaction、artifact placeholder、legacy shadow、authority、回滚和旧数据不删除规则，并验证非目标没有混入本批次范围

## 3. 依赖与 Gate

- [x] 3.1 在 change 说明中维护 C01→C02→C11 的依赖关系和 schema_version 升级回路，并通过 `openspec validate freeze-runtime-event-contract --type change --strict --no-interactive` 验证
- [x] 3.2 将 C01 的冻结结果作为 C02～C11 的输入，检查所有后续 proposal/spec/design/tasks 对字段和边界的引用一致
