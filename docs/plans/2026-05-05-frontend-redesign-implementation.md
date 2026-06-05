# 前端 UI 现代化重设计 — 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将暗色终端风前端重构为现代 AI 产品风双栏布局，支持亮/暗自动主题切换。

**Architecture:** 纯 CSS 变量驱动双主题 + 双栏 HTML 结构 + 系统字体栈。后端 API 零改动，JS 逻辑只改 DOM 选择器。

**Tech Stack:** Vanilla HTML/CSS/JS, CSS custom properties, `prefers-color-scheme`, Flexbox layout

**Design doc:** `docs/plans/2026-05-05-frontend-redesign.md`

---

### Task 1: 重写 style.css — CSS 变量 + 全局布局

**Files:**
- Modify: `src/mini_claude/web/templates/style.css`

**Step 1: 完全重写 style.css**

```css
/* Mini Claude Code Web — Modern AI-Product Theme */

/* ─── Theme Variables ───────────────────────────────────── */
:root {
  --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
  --font-mono: 'SF Mono', 'Cascadia Code', 'Consolas', 'Monaco', monospace;

  /* light */
  --bg: #ffffff;
  --bg-secondary: #f7f7f8;
  --surface: #ffffff;
  --border: #e5e5e7;
  --text: #1a1a2e;
  --text-secondary: #6b6b80;
  --accent: #6c5ce7;
  --accent-light: #a29bfe;
  --accent-bg: #f0eeff;
  --red: #e74c3c;
  --green: #27ae60;
  --yellow: #e0af68;
  --hover: #f0f0f3;
  --radius: 12px;
  --radius-sm: 8px;
  --radius-lg: 16px;
  --radius-xl: 24px;
  --shadow-sm: 0 1px 3px rgba(0,0,0,0.06);
  --shadow: 0 2px 12px rgba(0,0,0,0.06);
  --shadow-lg: 0 8px 32px rgba(0,0,0,0.1);
  --sidebar-w: 260px;
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
    --red: #f7768e;
    --green: #9ece6a;
    --yellow: #e0af68;
    --hover: #252536;
    --shadow-sm: 0 1px 3px rgba(0,0,0,0.2);
    --shadow: 0 2px 16px rgba(0,0,0,0.3);
    --shadow-lg: 0 8px 40px rgba(0,0,0,0.4);
  }
}

/* ─── Reset ─────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.6;
  height: 100vh;
  display: flex;
  overflow: hidden;
  -webkit-font-smoothing: antialiased;
}

a { color: var(--accent); text-decoration: none; }
a:hover { opacity: 0.8; }

/* ─── Layout ────────────────────────────────────────────── */
#app {
  display: flex;
  width: 100%;
  height: 100vh;
}

/* Sidebar */
#sidebar {
  width: var(--sidebar-w);
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  transition: transform 0.25s ease;
  z-index: 20;
}

#sidebar .sidebar-header {
  padding: 16px;
  font-weight: 700;
  font-size: 15px;
  color: var(--text);
  letter-spacing: -0.01em;
  flex-shrink: 0;
}

#sidebar .sidebar-header .logo-dot {
  display: inline-block;
  width: 8px; height: 8px;
  background: var(--accent);
  border-radius: 50%;
  margin-right: 8px;
  vertical-align: middle;
}

#sidebar .sidebar-body {
  flex: 1;
  overflow-y: auto;
  padding: 0 10px;
}

#sidebar .sidebar-footer {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

/* New chat button */
.btn-new-chat {
  display: block;
  width: calc(100% - 20px);
  margin: 0 10px 12px;
  padding: 10px 12px;
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  font-family: var(--font);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  text-align: center;
  transition: opacity 0.15s;
}
.btn-new-chat:hover { opacity: 0.85; }

/* Session list items */
.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background 0.15s;
  margin-bottom: 2px;
  border-left: 3px solid transparent;
}
.session-item:hover { background: var(--hover); }
.session-item.active {
  background: var(--accent-bg);
  border-left-color: var(--accent);
}
.session-item .session-info { flex: 1; overflow: hidden; }
.session-item .session-title {
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--text);
}
.session-item .session-date {
  font-size: 11px;
  color: var(--text-secondary);
  margin-top: 2px;
}
.session-item .session-delete {
  opacity: 0;
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 14px;
  padding: 2px 6px;
  border-radius: 4px;
  transition: opacity 0.15s, color 0.15s;
}
.session-item:hover .session-delete { opacity: 1; }
.session-item .session-delete:hover { color: var(--red); }

/* Sidebar footer link */
.sidebar-link {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-secondary);
  transition: color 0.15s;
  cursor: pointer;
}
.sidebar-link:hover { color: var(--text); }

/* Main area */
#main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

/* Top header bar */
#topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 20px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  background: var(--bg);
}
#topbar .current-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
}
#topbar .status-text {
  font-size: 12px;
  color: var(--text-secondary);
}
#topbar .hamburger {
  display: none;
  background: none;
  border: none;
  font-size: 20px;
  color: var(--text);
  cursor: pointer;
  padding: 4px;
  margin-right: 12px;
}

/* Messages area */
#messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  scroll-behavior: smooth;
}

/* Message bubbles */
.message {
  max-width: 80%;
  padding: 12px 18px;
  border-radius: var(--radius-lg);
  font-size: 14px;
  line-height: 1.6;
  animation: msg-in 0.25s ease;
  word-wrap: break-word;
}
@keyframes msg-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
.message.user {
  align-self: flex-end;
  background: var(--accent-bg);
  color: var(--text);
  border-bottom-right-radius: 6px;
}
.message.assistant {
  align-self: flex-start;
  background: var(--surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
  border-bottom-left-radius: 6px;
  width: 100%;
  max-width: 100%;
}

/* Tool card */
.tool-card {
  background: var(--bg-secondary);
  border-left: 3px solid var(--accent);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  margin: 10px 0;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.tool-card .tool-header {
  padding: 10px 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  user-select: none;
  transition: background 0.15s;
}
.tool-card .tool-header:hover { background: var(--hover); }
.tool-card .tool-icon { font-size: 15px; }
.tool-card .tool-name {
  font-weight: 600;
  color: var(--accent);
  font-family: var(--font-mono);
  font-size: 12px;
}
.tool-card .tool-summary {
  color: var(--text-secondary);
  font-size: 12px;
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tool-card .tool-chevron {
  font-size: 12px;
  color: var(--text-secondary);
  transition: transform 0.2s;
}
.tool-card.open .tool-chevron { transform: rotate(180deg); }
.tool-card .tool-body {
  padding: 0 14px;
  max-height: 0;
  overflow: hidden;
  transition: max-height 0.3s ease, padding 0.3s ease;
}
.tool-card.open .tool-body {
  max-height: 400px;
  overflow-y: auto;
  padding: 10px 14px;
}
.tool-body pre {
  white-space: pre-wrap;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.5;
  margin: 0;
}

/* Diff lines */
.diff-line { font-family: var(--font-mono); font-size: 12px; line-height: 1.5; white-space: pre-wrap; }
.diff-line.add { color: var(--green); }
.diff-line.remove { color: var(--red); }
.diff-line.context { color: var(--text-secondary); }

/* Spinner */
.spinner {
  display: inline-block;
  width: 15px; height: 15px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.5s linear infinite;
  flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* Input area */
#input-area {
  padding: 14px 24px 20px;
  background: var(--bg);
  flex-shrink: 0;
}
#input-area .shortcuts {
  display: flex;
  gap: 6px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
#input-area .shortcut {
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 3px 10px;
  cursor: pointer;
  transition: all 0.15s;
  font-family: var(--font-mono);
}
#input-area .shortcut:hover {
  color: var(--accent);
  border-color: var(--accent-light);
  background: var(--accent-bg);
}
#input-area .input-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}
#input-area textarea {
  flex: 1;
  background: var(--bg-secondary);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  padding: 12px 18px;
  font-family: var(--font);
  font-size: 14px;
  resize: none;
  min-height: 48px;
  max-height: 140px;
  line-height: 1.5;
  transition: border-color 0.2s, box-shadow 0.2s;
}
#input-area textarea:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-bg);
}
#input-area .btn-send {
  width: 48px; height: 48px;
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 50%;
  cursor: pointer;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: opacity 0.15s, transform 0.15s;
  flex-shrink: 0;
}
#input-area .btn-send:hover { opacity: 0.85; transform: scale(1.04); }
#input-area .btn-send:active { transform: scale(0.96); }

/* ─── Modal ─────────────────────────────────────────────── */
.modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  animation: fade-in 0.2s ease;
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
}
@keyframes fade-in { from { opacity: 0; } to { opacity: 1; } }
.modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 24px;
  max-width: 480px;
  width: 90%;
  box-shadow: var(--shadow-lg);
  animation: modal-in 0.2s ease;
}
@keyframes modal-in {
  from { opacity: 0; transform: scale(0.94); }
  to { opacity: 1; transform: scale(1); }
}
.modal h3 {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 14px;
  color: var(--text);
}
.modal .cmd {
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--text-secondary);
  background: var(--bg-secondary);
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  margin-bottom: 18px;
  word-break: break-all;
  line-height: 1.5;
}
.modal .actions { display: flex; gap: 10px; justify-content: flex-end; }

/* Buttons */
.btn {
  padding: 8px 18px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-family: var(--font);
  font-size: 13px;
  font-weight: 500;
  background: var(--surface);
  color: var(--text);
  transition: all 0.15s;
}
.btn:hover { background: var(--hover); }
.btn.primary {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}
.btn.primary:hover { opacity: 0.85; }
.btn.danger {
  background: var(--red);
  color: #fff;
  border-color: var(--red);
}
.btn.danger:hover { opacity: 0.85; }

/* ─── Admin tabs ────────────────────────────────────────── */
.admin-nav {
  display: flex;
  gap: 6px;
  padding: 12px 20px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.admin-tab {
  padding: 6px 16px;
  border-radius: 20px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  border: 1px solid transparent;
  transition: all 0.2s;
}
.admin-tab:hover { color: var(--text); background: var(--hover); }
.admin-tab.active {
  color: var(--accent);
  background: var(--accent-bg);
  border-color: var(--accent-light);
}

/* ─── Admin content ─────────────────────────────────────── */
.admin-content { flex: 1; overflow-y: auto; padding: 20px; }
.tab-panel { display: none; }
.tab-panel.active { display: block; }
.item-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
  margin-bottom: 10px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.15s;
}
.item-card:hover { box-shadow: var(--shadow); }
.item-card .info { flex: 1; }
.item-card .info .name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 4px;
}
.item-card .info .meta {
  font-size: 12px;
  color: var(--text-secondary);
}
.item-card .actions { display: flex; gap: 8px; flex-shrink: 0; }
.item-card.invalid { opacity: 0.4; }

/* ─── Banner ────────────────────────────────────────────── */
#banner {
  padding: 10px 20px;
  text-align: center;
  flex-shrink: 0;
  font-size: 13px;
  display: none;
}
#banner.error {
  background: var(--red);
  color: #fff;
  display: block;
}

/* ─── Toast ─────────────────────────────────────────────── */
#toast {
  position: fixed;
  bottom: 100px;
  right: 24px;
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 10px 18px;
  border-radius: var(--radius);
  font-size: 13px;
  box-shadow: var(--shadow-lg);
  z-index: 50;
  opacity: 0;
  transform: translateX(20px);
  transition: opacity 0.3s, transform 0.3s;
  pointer-events: none;
}
#toast.show {
  opacity: 1;
  transform: translateX(0);
}

/* ─── Scrollbar ─────────────────────────────────────────── */
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 10px;
}
::-webkit-scrollbar-thumb:hover { background: var(--text-secondary); }

/* ─── Responsive ────────────────────────────────────────── */
@media (max-width: 768px) {
  #sidebar {
    position: fixed;
    left: 0; top: 0; bottom: 0;
    transform: translateX(-100%);
    box-shadow: var(--shadow-lg);
  }
  #sidebar.open { transform: translateX(0); }
  #topbar .hamburger { display: block; }
  .message { max-width: 95%; }
}
```

**Step 2: 验证 CSS 语法**

```bash
# 检查大括号平衡
grep -c '{' src/mini_claude/web/templates/style.css
grep -c '}' src/mini_claude/web/templates/style.css
```

Expected: 两者数字相同。

**Step 3: Commit**

```bash
git add src/mini_claude/web/templates/style.css
git commit -m "feat: rewrite CSS with modern theme, dual layout, light/dark modes"
```

---

### Task 2: 重写 index.html — 双栏结构

**Files:**
- Modify: `src/mini_claude/web/templates/index.html`

**Step 1: 重写 index.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mini Claude Code</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<div id="app">

    <!-- Sidebar -->
    <aside id="sidebar">
        <div class="sidebar-header">
            <span class="logo-dot"></span>Mini Claude Code
        </div>
        <button class="btn-new-chat" id="btn-new-chat">+ 新对话</button>
        <div class="sidebar-body" id="session-list">
            <div style="padding:12px;color:var(--text-secondary);font-size:12px">暂无会话</div>
        </div>
        <div class="sidebar-footer">
            <a class="sidebar-link" href="/admin">管理面板 →</a>
        </div>
    </aside>

    <!-- Main -->
    <main id="main">
        <header id="topbar">
            <div style="display:flex;align-items:center">
                <button class="hamburger" id="hamburger" aria-label="菜单">☰</button>
                <span class="current-title" id="current-title">新对话</span>
            </div>
            <span class="status-text" id="status-info">Loading...</span>
        </header>

        <div id="banner" style="display:none"></div>

        <div id="messages"></div>

        <div id="input-area">
            <div class="shortcuts">
                <span class="shortcut" data-cmd="/clear">/clear</span>
                <span class="shortcut" data-cmd="/compact">/compact</span>
                <span class="shortcut" data-cmd="/cost">/cost</span>
                <span class="shortcut" data-cmd="/plan">/plan</span>
            </div>
            <div class="input-row">
                <textarea id="user-input" rows="1" placeholder="输入消息... (Enter 发送，Shift+Enter 换行)"></textarea>
                <button class="btn-send" id="send-btn" title="发送">↑</button>
            </div>
        </div>
    </main>

</div>

<div id="toast"></div>

<script src="/static/app.js"></script>
<script>initChat();</script>
</body>
</html>
```

**Step 2: 验证 HTML**

浏览器打开页面，确认双栏结构正常渲染，侧栏 260px，主区域撑满剩余空间。

**Step 3: Commit**

```bash
git add src/mini_claude/web/templates/index.html
git commit -m "feat: redesign chat page with sidebar layout"
```

---

### Task 3: 更新 app.js — 侧栏交互 + 选择器适配

**Files:**
- Modify: `src/mini_claude/web/templates/app.js`

**Step 1: 修改 initChat()，添加侧栏逻辑**

在 `initChat()` 函数中追加：

```javascript
function initChat() {
    // ... existing code (input, sendBtn, shortcuts, status check) stays ...

    // Sidebar: new chat
    document.getElementById('btn-new-chat').addEventListener('click', () => {
        fetch('/api/clear', { method: 'POST' });
        document.getElementById('messages').innerHTML = '';
        document.getElementById('current-title').textContent = '新对话';
        addToast('新对话已创建');
    });

    // Sidebar: hamburger menu for mobile
    document.getElementById('hamburger').addEventListener('click', () => {
        document.getElementById('sidebar').classList.toggle('open');
    });

    // Sidebar: close on main click (mobile)
    document.getElementById('main').addEventListener('click', () => {
        document.getElementById('sidebar').classList.remove('open');
    });

    // Load session list into sidebar
    loadSidebarSessions();
}
```

**Step 2: 添加侧栏会话列表函数**

在 `initChat` 后面添加：

```javascript
async function loadSidebarSessions() {
    try {
        const sessions = await API.fetchJSON('/api/sessions');
        const container = document.getElementById('session-list');
        if (!sessions.length) {
            container.innerHTML = '<div style="padding:12px;color:var(--text-secondary);font-size:12px">暂无会话</div>';
            return;
        }
        container.innerHTML = sessions.map(s => `
            <div class="session-item" data-id="${escapeHtml(s.id)}" onclick="switchSession('${escapeHtml(s.id)}')">
                <div class="session-info">
                    <div class="session-title">${escapeHtml(s.id)}</div>
                    <div class="session-date">${escapeHtml(s.start_time)}</div>
                </div>
                <button class="session-delete" onclick="event.stopPropagation();deleteSidebarSession('${escapeHtml(s.id)}')" title="删除">×</button>
            </div>
        `).join('');
    } catch(e) { /* silent */ }
}

async function switchSession(id) {
    const r = await API.fetchJSON(`/api/sessions/${id}/resume`, { method: 'POST' });
    if (r.ok) {
        document.getElementById('current-title').textContent = id;
        document.getElementById('messages').innerHTML = '';
        document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
        document.querySelector(`.session-item[data-id="${id}"]`)?.classList.add('active');
        addToast('会话已恢复');
    } else {
        addToast('恢复失败: ' + (r.error || 'unknown'));
    }
}

async function deleteSidebarSession(id) {
    await API.fetchJSON(`/api/sessions/${id}`, { method: 'DELETE' });
    loadSidebarSessions();
    addToast('会话已删除');
}
```

**Step 3: 修改 appendMessage 中的消息 class 格式**

将 `appendMessage` 函数中的 className 从反引号改为普通字符串（无变化，保持原样即可）。不需要改动。

**Step 4: Commit**

```bash
git add src/mini_claude/web/templates/app.js
git commit -m "feat: add sidebar session management and mobile hamburger"
```

---

### Task 4: 更新 admin.html — 适配新主题

**Files:**
- Modify: `src/mini_claude/web/templates/admin.html`

**Step 1: 重写 admin.html 为双栏布局**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mini Claude Code — 管理面板</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<div id="app">

    <aside id="sidebar">
        <div class="sidebar-header">
            <span class="logo-dot"></span>Mini Claude Code
        </div>
        <div class="sidebar-body" style="padding:10px">
            <a class="sidebar-link" href="/">← 返回聊天</a>
        </div>
    </aside>

    <main id="main">
        <header id="topbar">
            <span class="current-title">管理面板</span>
            <span class="status-text"></span>
        </header>

        <nav class="admin-nav">
            <div class="admin-tab active" data-tab="sessions">会话</div>
            <div class="admin-tab" data-tab="memories">记忆</div>
            <div class="admin-tab" data-tab="skills">技能</div>
            <div class="admin-tab" data-tab="config">配置</div>
        </nav>

        <div class="admin-content">
            <div id="tab-sessions" class="tab-panel active"><p style="color:var(--text-secondary)">Loading...</p></div>
            <div id="tab-memories" class="tab-panel"><p style="color:var(--text-secondary)">Loading...</p></div>
            <div id="tab-skills" class="tab-panel"><p style="color:var(--text-secondary)">Loading...</p></div>
            <div id="tab-config" class="tab-panel"><p style="color:var(--text-secondary)">Loading...</p></div>
        </div>
    </main>

</div>

<div id="toast"></div>

<script src="/static/app.js"></script>
<script>initAdmin();</script>
</body>
</html>
```

**Step 2: Commit**

```bash
git add src/mini_claude/web/templates/admin.html
git commit -m "feat: update admin panel with sidebar and pill tabs"
```

---

### Task 5: 端到端验证

**Step 1: 验证页面加载**

```bash
cd src && pip install -e .
mini-claude-py --web --port 8000
```

打开浏览器:
- `http://localhost:8000` — 双栏聊天页
- `http://localhost:8000/admin` — 管理面板
- 切换系统亮/暗主题 — 页面自动跟随

**Step 2: 验证功能**

- 发送消息 → SSE 流式正常
- 工具卡片 → 点击展开/折叠
- 侧栏新对话按钮 → 清空消息
- 移动端 (<768px) → 汉堡菜单
- 管理面板 tab pill 切换

**Step 3: Commit**

```bash
git add -A
git commit -m "chore: E2E verification tweaks"
```

---

## 实现顺序

1. Task 1 — style.css 完全重写（主题变量 + 布局 + 组件 + 动画）
2. Task 2 — index.html 双栏结构
3. Task 3 — app.js 侧栏交互
4. Task 4 — admin.html 适配
5. Task 5 — E2E 验证

每个任务独立可提交。
