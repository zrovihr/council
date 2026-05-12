// Council App — Frontend Logic

(() => {
  const chatInner = document.getElementById('chat-inner');
  const chatArea = document.getElementById('chat-area');
  const traceList = document.getElementById('trace-list');
  const msgInput = document.getElementById('msg-input');
  const sendBtn = document.getElementById('send-btn');
  const agentButtons = document.querySelectorAll('.agent-btn');
  const compactBtn = document.getElementById('compact-btn');
  const cancelBtn = document.getElementById('cancel-btn');
  const newMsgsBtn = document.getElementById('new-msgs-btn');
  const statusIndicator = document.getElementById('status-indicator');
  const projectNameEl = document.getElementById('project-name');
  const agentModelsEl = document.getElementById('agent-models');
  const configToggleBtn = document.getElementById('config-toggle-btn');
  const configPanel = document.getElementById('config-panel');
  const configGrid = document.getElementById('config-grid');
  const configStatus = document.getElementById('config-status');

  let userScrolledUp = false;
  let ws = null;
  let latestAgents = {};

  // ---- Marked + Highlight setup ----

  marked.setOptions({
    highlight: function (code, lang) {
      if (lang && hljs.getLanguage(lang)) {
        try {
          return hljs.highlight(code, { language: lang }).value;
        } catch (_) {}
      }
      try {
        return hljs.highlightAuto(code).value;
      } catch (_) {
        return code;
      }
    },
    breaks: true,
  });

  // ---- Scroll tracking ----

  chatArea.addEventListener('scroll', () => {
    const atBottom = chatArea.scrollHeight - chatArea.scrollTop - chatArea.clientHeight < 60;
    userScrolledUp = !atBottom;
    if (atBottom) {
      newMsgsBtn.classList.add('hidden');
    }
  });

  newMsgsBtn.addEventListener('click', () => {
    chatArea.scrollTo({ top: chatArea.scrollHeight, behavior: 'smooth' });
    newMsgsBtn.classList.add('hidden');
    userScrolledUp = false;
  });

  function scrollToBottom() {
    if (!userScrolledUp) {
      chatArea.scrollTop = chatArea.scrollHeight;
    } else {
      newMsgsBtn.classList.remove('hidden');
    }
  }

  // ---- Render ----

  function parseChatMD(text) {
    const turns = [];
    const TURN_HEADER_RE = /^##\s+\[@(\w+)\]\s+(.+)$/;
    const lines = text.split('\n');
    let current = null;

    for (const line of lines) {
      const m = TURN_HEADER_RE.exec(line);
      if (m) {
        if (current) turns.push(current);
        current = { author: m[1], time: m[2], body: '' };
      } else if (current) {
        if (line.trim() === '---') {
          turns.push(current);
          current = null;
        } else {
          if (current.body) current.body += '\n';
          current.body += line;
        }
      }
    }
    if (current) turns.push(current);
    return turns;
  }

  function renderTurns(turns) {
    chatInner.innerHTML = '';
    for (const turn of turns) {
      const card = document.createElement('div');
      card.className = 'turn-card';

      const header = document.createElement('div');
      header.className = 'turn-header';

      const authorSpan = document.createElement('span');
      authorSpan.className = `turn-author ${turn.author}`;
      authorSpan.textContent = `@${turn.author}`;

      const timeSpan = document.createElement('span');
      timeSpan.className = 'turn-time';
      timeSpan.textContent = turn.time;

      header.appendChild(authorSpan);
      header.appendChild(timeSpan);

      const body = document.createElement('div');
      body.className = 'turn-body';

      if (turn.body.trim()) {
        body.innerHTML = marked.parse(turn.body.trim());
      }

      card.appendChild(header);
      card.appendChild(body);
      chatInner.appendChild(card);
    }
    scrollToBottom();
  }

  // ---- API calls ----

  async function fetchChat() {
    try {
      const res = await fetch('/api/chat');
      const data = await res.json();
      const turns = parseChatMD(data.text);
      renderTurns(turns);
    } catch (e) {
      console.error('Failed to fetch chat:', e);
    }
  }

  async function sendMessage() {
    const text = msgInput.value.trim();
    if (!text) return;

    msgInput.value = '';
    try {
      const res = await fetch('/api/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) {
        const err = await res.json();
        console.error('Send failed:', err.error);
      }
    } catch (e) {
      console.error('Send error:', e);
    }
  }

  async function compactChat() {
    try {
      await fetch('/api/compact', { method: 'POST' });
    } catch (e) {
      console.error('Compact failed:', e);
    }
  }

  async function cancelDispatch() {
    try {
      await fetch('/api/cancel', { method: 'POST' });
    } catch (e) {
      console.error('Cancel failed:', e);
    }
  }

  async function fetchStatus() {
    try {
      const res = await fetch('/api/status');
      const data = await res.json();
      updateStatus(data);
    } catch (_) {}
  }

  async function patchConfig(changes) {
    configStatus.textContent = 'saving...';
    try {
      const res = await fetch('/api/config', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changes),
      });
      if (!res.ok) {
        const err = await res.json();
        configStatus.textContent = err.error || 'save failed';
        return;
      }
      const data = await res.json();
      if (data.agents) {
        latestAgents = data.agents;
        renderAgentModels(latestAgents);
        renderConfig(latestAgents);
      }
      configStatus.textContent = 'saved';
      setTimeout(() => {
        if (configStatus.textContent === 'saved') configStatus.textContent = '';
      }, 1200);
    } catch (_) {
      configStatus.textContent = 'save failed';
    }
  }

  async function fetchTrace() {
    try {
      const res = await fetch('/api/trace');
      const data = await res.json();
      renderTrace(data.events || []);
    } catch (_) {}
  }

  function renderTrace(events) {
    traceList.innerHTML = '';
    for (const event of events) {
      appendTraceEvent(event);
    }
  }

  function appendTraceEvent(event) {
    const item = document.createElement('div');
    item.className = 'trace-item';

    const meta = document.createElement('div');
    meta.className = 'trace-meta';
    meta.textContent = `${event.time || ''} @${event.agent || 'system'}`;

    const message = document.createElement('div');
    message.className = 'trace-message';
    message.textContent = event.message || '';

    item.appendChild(meta);
    item.appendChild(message);

    if (event.detail) {
      const detail = document.createElement('div');
      detail.className = 'trace-detail';
      detail.textContent = event.detail;
      item.appendChild(detail);
    }

    traceList.appendChild(item);
    while (traceList.children.length > 100) {
      traceList.removeChild(traceList.firstChild);
    }
    traceList.scrollTop = traceList.scrollHeight;
  }

  // ---- WebSocket ----

  function connectWS() {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${protocol}://${window.location.host}/ws`;

    ws = new WebSocket(url);

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'chat_update') {
          fetchChat();
        } else if (msg.type === 'status') {
          updateStatus(msg);
        } else if (msg.type === 'trace_update') {
          appendTraceEvent(msg.event || {});
        } else if (msg.type === 'error') {
          console.error('Server error:', msg.msg);
        }
      } catch (_) {}
    };

    ws.onclose = () => {
      setTimeout(connectWS, 2000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }

  // ---- Status ----

  function updateStatus(data) {
    projectNameEl.textContent = data.project || '';
    latestAgents = data.agents || {};
    renderAgentModels(latestAgents);
    renderConfig(latestAgents);

    if (data.busy) {
      statusIndicator.className = 'busy';
      statusIndicator.innerHTML = '&#9679; @' + (data.agent || '?') + ' thinking&hellip;';
      cancelBtn.classList.remove('hidden');
    } else {
      statusIndicator.className = 'idle';
      statusIndicator.innerHTML = '&#9679; idle';
      cancelBtn.classList.add('hidden');
    }
  }

  function renderAgentModels(agents) {
    const order = ['claude', 'codex', 'deepseek'];
    agentModelsEl.innerHTML = '';
    for (const id of order) {
      const info = agents[id];
      if (!info) continue;
      const pill = document.createElement('span');
      pill.className = `agent-model ${id}`;
      pill.title = `${info.runtime || ''} — ${info.note || ''}`.trim();
      const effort = info.effort ? ` / ${info.effort}` : '';
      pill.textContent = `@${id}: ${info.model || info.runtime || 'default'}${effort}`;
      agentModelsEl.appendChild(pill);
    }
  }

  function optionList(options, current) {
    const seen = new Set();
    const values = [];
    if (current) values.push(current);
    for (const opt of options || []) values.push(opt);
    return values.filter((value) => {
      if (!value || seen.has(value)) return false;
      seen.add(value);
      return true;
    });
  }

  function makeSelect(agentId, section, value, options) {
    const select = document.createElement('select');
    select.dataset.agent = agentId;
    select.dataset.section = section;
    for (const opt of optionList(options, value)) {
      const option = document.createElement('option');
      option.value = opt;
      option.textContent = opt;
      select.appendChild(option);
    }
    select.value = value || '';
    select.addEventListener('change', () => {
      patchConfig({ [section]: { [agentId]: select.value } });
    });
    return select;
  }

  function renderConfig(agents) {
    if (configPanel.classList.contains('hidden')) return;
    const order = ['claude', 'codex', 'deepseek'];
    configGrid.innerHTML = '';
    for (const id of order) {
      const info = agents[id];
      if (!info) continue;

      const row = document.createElement('div');
      row.className = `config-row ${id}`;

      const label = document.createElement('div');
      label.className = 'config-agent';
      label.textContent = `@${id}`;

      const modelWrap = document.createElement('label');
      modelWrap.textContent = 'Model';
      modelWrap.appendChild(makeSelect(id, 'models', info.model || '', info.model_options || []));

      const effortWrap = document.createElement('label');
      effortWrap.textContent = 'Effort';
      effortWrap.appendChild(makeSelect(id, 'effort', info.effort || '', info.effort_options || []));

      const roleWrap = document.createElement('label');
      roleWrap.className = 'role-wrap';
      roleWrap.textContent = 'Role';
      const role = document.createElement('textarea');
      role.rows = 2;
      role.value = info.role || '';
      role.addEventListener('change', () => {
        patchConfig({ roles: { [id]: role.value } });
      });
      roleWrap.appendChild(role);

      row.appendChild(label);
      row.appendChild(modelWrap);
      row.appendChild(effortWrap);
      row.appendChild(roleWrap);
      configGrid.appendChild(row);
    }
  }

  // ---- @ Autocomplete ----

  const acBox = document.createElement('div');
  acBox.id = 'autocomplete-box';
  acBox.className = 'hidden';
  document.body.appendChild(acBox);

  let acState = {
    open: false,
    items: [],          // [{label, insert, kind}]
    selectedIdx: 0,
    triggerStart: -1,   // index of '@' in textarea value
    query: '',
  };

  function closeAC() {
    acState.open = false;
    acState.items = [];
    acState.selectedIdx = 0;
    acState.triggerStart = -1;
    acState.query = '';
    acBox.classList.add('hidden');
    acBox.innerHTML = '';
  }

  function renderAC() {
    acBox.innerHTML = '';
    if (acState.items.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'ac-empty';
      empty.textContent = 'No matches';
      acBox.appendChild(empty);
      return;
    }
    acState.items.forEach((it, i) => {
      const row = document.createElement('div');
      row.className = 'ac-row' + (i === acState.selectedIdx ? ' selected' : '');
      const kind = document.createElement('span');
      kind.className = 'ac-kind ac-kind-' + it.kind;
      kind.textContent = it.kind === 'agent' ? 'agent' : 'file';
      const label = document.createElement('span');
      label.className = 'ac-label';
      label.textContent = it.label;
      row.appendChild(kind);
      row.appendChild(label);
      row.addEventListener('mousedown', (e) => {
        e.preventDefault();
        acState.selectedIdx = i;
        applyAC();
      });
      acBox.appendChild(row);
    });
  }

  function positionAC() {
    const r = msgInput.getBoundingClientRect();
    acBox.style.left = r.left + 'px';
    acBox.style.bottom = (window.innerHeight - r.top + 4) + 'px';
    acBox.style.width = Math.min(r.width, 480) + 'px';
  }

  async function fetchCompletions(query) {
    try {
      const res = await fetch('/api/completions?q=' + encodeURIComponent(query));
      return await res.json();
    } catch (_) {
      return { agents: [], files: [] };
    }
  }

  function buildItems(data, query) {
    const items = [];
    for (const a of (data.agents || [])) {
      items.push({ label: a, insert: a, kind: 'agent' });
    }
    for (const f of (data.files || [])) {
      items.push({ label: f, insert: f, kind: 'file' });
    }
    return items.slice(0, 40);
  }

  async function refreshAC() {
    if (!acState.open) return;
    const data = await fetchCompletions(acState.query);
    acState.items = buildItems(data, acState.query);
    if (acState.selectedIdx >= acState.items.length) {
      acState.selectedIdx = 0;
    }
    positionAC();
    renderAC();
    acBox.classList.remove('hidden');
  }

  function applyAC() {
    if (!acState.open || acState.items.length === 0) {
      closeAC();
      return;
    }
    const it = acState.items[acState.selectedIdx];
    const val = msgInput.value;
    const before = val.slice(0, acState.triggerStart);
    const afterStart = acState.triggerStart + 1 + acState.query.length;
    const after = val.slice(afterStart);
    const inserted = '@' + it.insert + ' ';
    msgInput.value = before + inserted + after;
    const caret = (before + inserted).length;
    msgInput.setSelectionRange(caret, caret);
    closeAC();
    msgInput.focus();
  }

  function updateACFromInput() {
    const val = msgInput.value;
    const caret = msgInput.selectionStart;
    // Walk back from caret to find an '@' that isn't followed by whitespace.
    let i = caret - 1;
    let trigger = -1;
    while (i >= 0) {
      const ch = val[i];
      if (ch === '@') {
        // must be at start-of-string or preceded by whitespace
        if (i === 0 || /\s/.test(val[i - 1])) {
          trigger = i;
        }
        break;
      }
      if (/\s/.test(ch)) break;
      i--;
    }
    if (trigger === -1) {
      if (acState.open) closeAC();
      return;
    }
    const query = val.slice(trigger + 1, caret);
    if (/\s/.test(query)) {
      if (acState.open) closeAC();
      return;
    }
    acState.open = true;
    acState.triggerStart = trigger;
    acState.query = query;
    refreshAC();
  }

  msgInput.addEventListener('input', updateACFromInput);
  msgInput.addEventListener('click', updateACFromInput);
  msgInput.addEventListener('keyup', (e) => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) {
      updateACFromInput();
    }
  });
  msgInput.addEventListener('blur', () => {
    // delay so mousedown on row can fire first
    setTimeout(closeAC, 150);
  });

  // ---- Input events ----

  msgInput.addEventListener('keydown', (e) => {
    if (acState.open && acState.items.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        acState.selectedIdx = (acState.selectedIdx + 1) % acState.items.length;
        renderAC();
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        acState.selectedIdx = (acState.selectedIdx - 1 + acState.items.length) % acState.items.length;
        renderAC();
        return;
      }
      if (e.key === 'Tab' || e.key === 'Enter') {
        e.preventDefault();
        applyAC();
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        closeAC();
        return;
      }
    }
    if (e.ctrlKey && e.key === 'Enter') {
      e.preventDefault();
      sendMessage();
    }
  });

  sendBtn.addEventListener('click', sendMessage);
  compactBtn.addEventListener('click', compactChat);
  cancelBtn.addEventListener('click', cancelDispatch);
  configToggleBtn.addEventListener('click', () => {
    configPanel.classList.toggle('hidden');
    renderConfig(latestAgents);
  });
  agentButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      const mention = '@' + btn.dataset.agent;
      const text = msgInput.value.trim();
      msgInput.value = text ? `${mention} ${text}` : `${mention} `;
      msgInput.focus();
      msgInput.setSelectionRange(msgInput.value.length, msgInput.value.length);
    });
  });

  // ---- Init ----

  fetchChat();
  fetchStatus();
  fetchTrace();
  connectWS();
})();
