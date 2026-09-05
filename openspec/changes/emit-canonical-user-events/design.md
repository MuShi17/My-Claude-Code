## Decisions

1. chat 入口在 provider loop 前发射一次原始用户事件，使用当前 RunContext。
2. memory injection 作为同一 user turn 的受控 metadata/action，不能丢失原始 user content。
3. tool result、summary 和 system diagnostic 不伪装成用户原始输入。
4. canonical emitter 不可用时遵守既有 authority gate，不静默声称 canonical 完整。
