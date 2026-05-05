// Mini Claude Code Web — Frontend logic

const API = {
    async chat(message) {
        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message }),
        });
        if (!resp.ok) {
            const text = await resp.text();
            throw new Error(text || 'Chat failed');
        }
        return resp.body.getReader();
    },
    async confirm(id, approved) {
        const resp = await fetch('/api/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, approved }),
        });
        return resp.json();
    },
    async fetchJSON(url, opts = {}) {
        const resp = await fetch(url, opts);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json();
    },
};

// ─── Chat page ──────────────────────────────────────────────

let _currentAssistantDiv = null;
let _toolCardMap = {};

function initChat() {
    const input = document.getElementById('user-input');
    const sendBtn = document.getElementById('send-btn');

    sendBtn.addEventListener('click', sendMessage);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Shortcut buttons
    document.querySelectorAll('.shortcut').forEach(btn => {
        btn.addEventListener('click', () => {
            const cmd = btn.dataset.cmd;
            if (cmd === '/plan') {
                fetch('/api/plan/toggle', { method: 'POST' })
                    .then(r => r.json())
                    .then(d => addToast('Plan mode: ' + d.mode));
            } else {
                fetch(`/api/${cmd.replace('/', '')}`, { method: 'POST' })
                    .then(r => r.json())
                    .then(d => { if (d.ok !== false) addToast(`${cmd} done`); });
            }
        });
    });

    // Check agent status on load
    fetch('/api/status')
        .then(r => r.json())
        .then(s => {
            if (!s.ready) {
                showBanner('Agent is not ready — check API key configuration', 'error');
            }
            updateStatusBar(s);
        })
        .catch(() => showBanner('Cannot reach server', 'error'));

    // Sidebar: new chat
    const btnNewChat = document.getElementById('btn-new-chat');
    if (btnNewChat) {
        btnNewChat.addEventListener('click', () => {
            fetch('/api/clear', { method: 'POST' });
            document.getElementById('messages').innerHTML = '';
            document.getElementById('current-title').textContent = '新对话';
            addToast('新对话已创建');
        });
    }

    // Sidebar: hamburger menu for mobile
    const hamburger = document.getElementById('hamburger');
    if (hamburger) {
        hamburger.addEventListener('click', () => {
            document.getElementById('sidebar').classList.toggle('open');
        });
    }

    // Sidebar: close on main click (mobile)
    const main = document.getElementById('main');
    if (main) {
        main.addEventListener('click', () => {
            document.getElementById('sidebar').classList.remove('open');
        });
    }

    // Load session list into sidebar
    loadSidebarSessions();
}

async function sendMessage() {
    const input = document.getElementById('user-input');
    const message = input.value.trim();
    if (!message) return;
    input.value = '';
    input.focus();

    // Add user message bubble
    appendMessage('user', message);

    // Update current title from first message content
    const title = message.slice(0, 40) + (message.length > 40 ? '...' : '');
    const titleEl = document.getElementById('current-title');
    if (titleEl) titleEl.textContent = title;

    // Create assistant message container
    _currentAssistantDiv = appendMessage('assistant', '');
    // Show spinner while waiting
    const spinner = document.createElement('span');
    spinner.className = 'spinner';
    _currentAssistantDiv.appendChild(spinner);
    _toolCardMap = {};

    try {
        const reader = await API.chat(message);
        const decoder = new TextDecoder();
        let buffer = '';
        let currentEvent = null;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('event: ')) {
                    currentEvent = line.slice(7).trim();
                } else if (line.startsWith('data: ') && currentEvent) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        handleSSE(currentEvent, data);
                    } catch (e) {
                        // skip malformed
                    }
                    currentEvent = null;
                }
            }
        }
    } catch (e) {
        appendError('Connection error: ' + e.message);
    }

    _currentAssistantDiv = null;
    updateStatus();
    // Refresh sidebar session list
    setTimeout(() => loadSidebarSessions(), 500);
}

function handleSSE(type, data) {
    switch (type) {
        case 'text':
            appendText(data);
            break;
        case 'tool':
            addToolCard(data.name, data.input);
            break;
        case 'result':
            updateToolResult(data.tool, data.content);
            break;
        case 'diff':
            updateToolDiff(data.tool, data.file, data.lines);
            break;
        case 'confirm':
            showConfirmModal(data.id, data.command);
            break;
        case 'error':
            appendError(data);
            break;
        case 'info':
            addToast(data);
            break;
        case 'cost':
            updateStatusBarCost(data);
            break;
        case 'done':
            break;
    }
}

function appendMessage(role, text) {
    const msgs = document.getElementById('messages');
    const div = document.createElement('div');
    div.className = `message ${role}`;
    if (text) div.textContent = text;
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
    return div;
}

function appendText(text) {
    if (_currentAssistantDiv) {
        const spinner = _currentAssistantDiv.querySelector('.spinner');
        if (spinner) spinner.remove();
        _currentAssistantDiv.textContent += text;
        document.getElementById('messages').scrollTop = document.getElementById('messages').scrollHeight;
    }
}

function addToolCard(name, input) {
    const msgs = document.getElementById('messages');
    const summary = getToolSummary(name, input);
    const card = document.createElement('div');
    card.className = 'tool-card';
    card.innerHTML = `
        <div class="tool-header" onclick="this.parentElement.classList.toggle('open')">
            <span class="tool-icon">${getToolIcon(name)}</span>
            <span class="tool-name">${escapeHtml(name)}</span>
            <span class="tool-summary">${escapeHtml(summary)}</span>
            <span class="spinner" style="margin-left:auto"></span>
        </div>
        <div class="tool-body"><pre style="color:var(--dim)">Running...</pre></div>
    `;
    msgs.appendChild(card);
    msgs.scrollTop = msgs.scrollHeight;
    return card;
}

function updateToolResult(toolName, content) {
    const cards = document.querySelectorAll('.tool-card');
    const card = cards[cards.length - 1];
    if (card) {
        const body = card.querySelector('.tool-body');
        body.innerHTML = `<pre style="white-space:pre-wrap;color:var(--dim);font-size:12px">${escapeHtml(content)}</pre>`;
        const spinner = card.querySelector('.spinner');
        if (spinner) spinner.remove();
    }
}

function updateToolDiff(toolName, file, lines) {
    const cards = document.querySelectorAll('.tool-card');
    const card = cards[cards.length - 1];
    if (card) {
        const body = card.querySelector('.tool-body');
        let html = `<div style="color:var(--dim);font-size:12px;margin-bottom:4px">${escapeHtml(file)}</div>`;
        for (const line of lines) {
            let cls = 'context';
            if (line.startsWith('+')) cls = 'add';
            else if (line.startsWith('-')) cls = 'remove';
            else if (line.startsWith('@@')) cls = 'context';
            html += `<div class="diff-line ${cls}">${escapeHtml(line)}</div>`;
        }
        body.innerHTML = html;
        const spinner = card.querySelector('.spinner');
        if (spinner) spinner.remove();
    }
}

function showConfirmModal(id, command) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.innerHTML = `
            <div class="modal">
                <h3>&#9888; Dangerous Command</h3>
                <div class="cmd">${escapeHtml(command)}</div>
                <div class="actions">
                    <button class="btn danger deny">Deny</button>
                    <button class="btn primary allow">Allow</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        overlay.querySelector('.allow').addEventListener('click', async () => {
            document.body.removeChild(overlay);
            await API.confirm(id, true);
        });
        overlay.querySelector('.deny').addEventListener('click', async () => {
            document.body.removeChild(overlay);
            await API.confirm(id, false);
        });
    });
}

function appendError(text) {
    if (_currentAssistantDiv) {
        const span = document.createElement('span');
        span.style.color = 'var(--red)';
        span.textContent = '\n' + text;
        _currentAssistantDiv.appendChild(span);
    }
}

function showBanner(msg, level) {
    const banner = document.getElementById('banner');
    if (banner) {
        banner.textContent = msg;
        banner.className = level;
        banner.style.display = 'block';
    }
}

function addToast(msg) {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.className = 'show';
    setTimeout(() => { toast.className = ''; }, 2500);
}

async function updateStatus() {
    try {
        const s = await API.fetchJSON('/api/status');
        updateStatusBar(s);
    } catch(e) {}
}

function updateStatusBar(s) {
    const el = document.getElementById('status-info');
    if (el) el.textContent = `${s.model} | ${s.permission_mode} | ${s.session_id}`;
}

function updateStatusBarCost(data) {
    const el = document.getElementById('status-info');
    if (el) {
        const cost = data.estimated_cost_usd != null
            ? data.estimated_cost_usd.toFixed(4)
            : ((data.input / 1_000_000) * 3 + (data.output / 1_000_000) * 15).toFixed(4);
        el.textContent = `Tokens: ${data.input}/${data.output} | $${cost}`;
    }
}

function getToolIcon(name) {
    const icons = { read_file: '\u{1F4D6}', write_file: '\u{270F}\u{FE0F}', edit_file: '\u{1F527}', list_files: '\u{1F4C1}', grep_search: '\u{1F50D}', run_shell: '\u{1F4BB}', skill: '\u{26A1}', agent: '\u{1F916}' };
    return icons[name] || '\u{1F528}';
}

function getToolSummary(name, input) {
    if (name === 'read_file' || name === 'write_file' || name === 'edit_file') return input.file_path || '';
    if (name === 'run_shell') return (input.command || '').slice(0, 60);
    if (name === 'grep_search') return `"${input.pattern || ''}" in ${input.path || '.'}`;
    return '';
}

function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// ─── Sidebar sessions ──────────────────────────────────────

async function loadSidebarSessions() {
    try {
        const sessions = await API.fetchJSON('/api/sessions');
        const container = document.getElementById('session-list');
        if (!container) return;
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
                <button class="session-delete" onclick="event.stopPropagation();deleteSidebarSession('${escapeHtml(s.id)}')" title="删除">&times;</button>
            </div>
        `).join('');
    } catch(e) { /* silent - sidebar sessions are non-critical */ }
}

async function switchSession(id) {
    const r = await API.fetchJSON(`/api/sessions/${id}/resume`, { method: 'POST' });
    if (r.ok) {
        document.getElementById('current-title').textContent = id;
        document.getElementById('messages').innerHTML = '';
        document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
        const el = document.querySelector(`.session-item[data-id="${id}"]`);
        if (el) el.classList.add('active');

        // Load and render historical messages
        try {
            const mr = await API.fetchJSON(`/api/sessions/${id}/messages`);
            if (mr.ok && mr.messages) {
                for (const msg of mr.messages) {
                    appendMessage(msg.role, msg.content);
                }
            }
        } catch(e) { /* messages are best-effort */ }

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

// ─── Admin page ─────────────────────────────────────────────

function initAdmin() {
    // Tab switching
    document.querySelectorAll('.admin-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
        });
    });

    // Load data
    loadSessions();
    loadMemories();
    loadSkills();
    loadConfig();
}

async function loadSessions() {
    const container = document.getElementById('tab-sessions');
    try {
        const sessions = await API.fetchJSON('/api/sessions');
        if (!sessions.length) {
            container.innerHTML = '<p style="color:var(--dim)">No sessions found.</p>';
            return;
        }
        container.innerHTML = sessions.map(s => `
            <div class="item-card ${s.valid ? '' : 'invalid'}">
                <div class="info">
                    <div class="name">${escapeHtml(s.id)}</div>
                    <div class="meta">${escapeHtml(s.start_time)} | ${escapeHtml(s.model)} | ${escapeHtml(s.cwd)} | ${s.message_count} msgs ${!s.valid ? '| &#9888; corrupted' : ''}</div>
                </div>
                <div class="actions">
                    <button class="btn" onclick="resumeSession('${escapeHtml(s.id)}')">Resume</button>
                    <button class="btn danger" onclick="deleteSession('${escapeHtml(s.id)}')">Delete</button>
                </div>
            </div>
        `).join('');
    } catch(e) {
        container.innerHTML = '<p style="color:var(--red)">Failed to load sessions.</p>';
    }
}

async function resumeSession(id) {
    const r = await API.fetchJSON(`/api/sessions/${id}/resume`, { method: 'POST' });
    addToast(r.ok ? 'Session resumed' : 'Failed: ' + (r.error || 'unknown'));
}

async function deleteSession(id) {
    await API.fetchJSON(`/api/sessions/${id}`, { method: 'DELETE' });
    loadSessions();
    addToast('Session deleted');
}

async function loadMemories() {
    const container = document.getElementById('tab-memories');
    try {
        const memories = await API.fetchJSON('/api/memories');
        if (!memories.length) {
            container.innerHTML = '<p style="color:var(--dim)">No memories saved.</p>';
            return;
        }
        container.innerHTML = memories.map(m => `
            <div class="item-card">
                <div class="info">
                    <div class="name">[${escapeHtml(m.type)}] ${escapeHtml(m.name)}</div>
                    <div class="meta">${escapeHtml(m.description)} | ${escapeHtml(m.filename)}</div>
                </div>
                <div class="actions">
                    <button class="btn" onclick="viewMemory('${escapeHtml(m.filename)}')">View</button>
                    <button class="btn danger" onclick="deleteMemory('${escapeHtml(m.filename)}')">Delete</button>
                </div>
            </div>
        `).join('');
    } catch(e) {
        container.innerHTML = '<p style="color:var(--red)">Failed to load memories.</p>';
    }
}

async function viewMemory(filename) {
    try {
        const r = await API.fetchJSON(`/api/memories/${filename}`);
        if (r.ok) {
            alert(r.content);
        } else {
            addToast(r.error || 'Not found');
        }
    } catch(e) {
        addToast('Failed to load memory');
    }
}

async function deleteMemory(filename) {
    await API.fetchJSON(`/api/memories/${filename}`, { method: 'DELETE' });
    loadMemories();
    addToast('Memory deleted');
}

async function loadSkills() {
    const container = document.getElementById('tab-skills');
    try {
        const skills = await API.fetchJSON('/api/skills');
        if (!skills.length) {
            container.innerHTML = '<p style="color:var(--dim)">No skills found.</p>';
            return;
        }
        container.innerHTML = skills.map(s => `
            <div class="item-card">
                <div class="info">
                    <div class="name">${s.user_invocable ? '/' + escapeHtml(s.name) : escapeHtml(s.name)}</div>
                    <div class="meta">${escapeHtml(s.description)} | source: ${escapeHtml(s.source)} | context: ${escapeHtml(s.context)}</div>
                </div>
            </div>
        `).join('');
    } catch(e) {
        container.innerHTML = '<p style="color:var(--red)">Failed to load skills.</p>';
    }
}

async function loadConfig() {
    const container = document.getElementById('tab-config');
    try {
        const status = await API.fetchJSON('/api/status');
        const cost = await API.fetchJSON('/api/cost');
        container.innerHTML = `
            <div class="item-card"><div class="info"><div class="name">Model</div><div class="meta">${escapeHtml(status.model)}</div></div></div>
            <div class="item-card"><div class="info"><div class="name">Permission Mode</div><div class="meta">${escapeHtml(status.permission_mode)}</div></div></div>
            <div class="item-card"><div class="info"><div class="name">Session ID</div><div class="meta">${escapeHtml(status.session_id)}</div></div></div>
            <div class="item-card"><div class="info"><div class="name">Tokens</div><div class="meta">In: ${cost.input_tokens} | Out: ${cost.output_tokens} | Cost: $${cost.estimated_cost_usd}</div></div></div>
            <div class="item-card"><div class="info"><div class="name">Turns</div><div class="meta">${cost.turns}${cost.max_turns ? ' / ' + cost.max_turns : ''}</div></div></div>
        `;
    } catch(e) {
        container.innerHTML = '<p style="color:var(--red)">Failed to load config.</p>';
    }
}
