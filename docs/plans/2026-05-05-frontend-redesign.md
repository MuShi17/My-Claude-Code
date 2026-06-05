# 前端 UI 现代化重设计 — 设计文档

**日期**: 2026-05-05
**状态**: 已确认

## 概述

将当前暗色终端风格的 Web 前端重构为现代 AI 产品风格，提升视觉品质和用户体验。

## 设计原则

- AI 产品风：圆角卡片、柔和阴影、现代字体
- 双栏布局：左侧会话列表 + 右侧聊天区
- 自动主题：CSS 变量 + `prefers-color-scheme` 亮/暗自动切换
- 零外部依赖：系统字体栈，不引入 Google Fonts 或 Tailwind CDN
- JS 逻辑不变：只改样式和 DOM 结构，后端 API 零改动

## 配色系统

```css
:root {
  /* 亮色 */
  --bg: #ffffff;
  --bg-secondary: #f7f7f8;
  --surface: #ffffff;
  --border: #e5e5e7;
  --text: #1a1a2e;
  --text-secondary: #6b6b80;
  --accent: #6c5ce7;           /* 紫色主色调 */
  --accent-light: #a29bfe;
  --accent-bg: #f0eeff;
  --hover: #f0f0f3;
  --radius: 12px;
  --shadow: 0 2px 12px rgba(0,0,0,0.06);
  --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --font-mono: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f0f14;
    --bg-secondary: #1a1a24;
    --surface: #1e1e2a;
    --border: #2d2d3d;
    --text: #e8e8ed;
    --text-secondary: #8888a0;
    --accent: #7c6ff7;
    --accent-light: #a29bfe;
    --accent-bg: #1e1a3a;
    --hover: #252536;
    --shadow: 0 2px 16px rgba(0,0,0,0.3);
  }
}
```

## 布局：双栏结构

```
┌──────────┬───────────────────────────────────────────┐
│ 260px    │                                          │
│          │  Header                                  │
│ 会话列表  ├───────────────────────────────────────────┤
│          │                                          │
│ + 新对话  │  消息区域                                 │
│          │                                          │
│ ▸ bug    │  ┌ User ────────────────────────────┐   │
│   fix    │  │                                   │   │
│  重构     │  └──────────────────────────────────┘   │
│          │  ┌ 🤖 ──────────────────────────────┐   │
│ 管理面板  │  │                                   │   │
│          │  │  ┌ tool card ───────────────┐    │   │
│          │  │  │                           │    │   │
│          │  │  └───────────────────────────┘    │   │
│          │  └──────────────────────────────────┘   │
│          ├───────────────────────────────────────────┤
│          │  输入区域                                 │
└──────────┴───────────────────────────────────────────┘
```

**响应式：** <768px 时侧栏隐藏，汉堡菜单唤出。

## 侧边栏

- 宽度：260px
- 顶部标题 + 新建对话按钮（accent 色圆角按钮）
- 会话列表：每项标题 + 日期，hover 变色，活跃项左边框 3px accent + accent-bg
- 底部管理面板链接
- 超出滚动

## 聊天消息

- User：右对齐，accent-bg 底色，圆角 16px
- AI：左对齐，surface 底色 + shadow 阴影
- 工具卡片：bg-secondary 底色，左边框 accent 色，默认折叠点击展开
- 代码块/工具结果：font-mono 等宽字体
- 间距 20px

## 输入区域

- 大圆角输入框 (24px)，bg-secondary 底色，min-height 48px
- 发送按钮 accent 实心
- 快捷命令标签移到上方一行

## 管理面板

- 复用同一侧边栏
- Tab 改为 pill 药丸样式
- Item card 增加 shadow，加大圆角

## 交互 & 动画

| 交互 | 行为 |
|------|------|
| 消息出现 | slide-up 0.2s |
| 工具卡片展开 | max-height 过渡 0.25s |
| Toast | 右侧滑入 0.3s，3s 后滑出 |
| 侧栏 hover | 背景色 0.15s |
| 确认弹窗 | 缩放 + 淡入 0.2s |
| 主题切换 | 自动跟随系统 |
| 移动端 | 汉堡菜单唤出侧栏 |

## 改动范围

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `style.css` | 完全重写 | 双主题变量、双栏布局、全部组件、动画 |
| `index.html` | 重写结构 | 单栏 → 双栏 |
| `admin.html` | 微调结构 | Tab pill 样式，适配新 class |
| `app.js` | 微调 | 调整 DOM 选择器、新增侧栏交互 |
| 后端 API | 零改动 | 完全兼容现有 SSE/API |

## 规划

下一步：实现计划
