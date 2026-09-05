## 1. Implementation

- [x] 1.1 增加 canonical projection 到 provider-neutral context 的 adapter
- [x] 1.2 在 Anthropic/OpenAI 下一轮请求前接入 adapter，并保留明确 shadow comparator

## 2. Verification

- [x] 2.1 增加清空/扰动 legacy message arrays 后的 canonical-first loop 测试
- [x] 2.2 验证 tool call/result、partial、hidden event 和双 provider parity
