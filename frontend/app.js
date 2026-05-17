// Council App - Multi-session frontend logic

(() => {
  const chatInner = document.getElementById('chat-inner');
  const chatArea = document.getElementById('chat-area');
  const traceList = document.getElementById('trace-list');
  const msgInput = document.getElementById('msg-input');
  const sendBtn = document.getElementById('send-btn');
  const agentButtons = document.querySelectorAll('.agent-btn');
  const compactBtn = document.getElementById('compact-btn');
  const eraseBtn = document.getElementById('erase-btn');
  const cancelBtn = document.getElementById('cancel-btn');
  const newMsgsBtn = document.getElementById('new-msgs-btn');
  const confirmOverlay = document.getElementById('confirm-overlay');
  const confirmTitle = document.getElementById('confirm-title');
  const confirmMsg = document.getElementById('confirm-msg');
  const confirmYesBtn = document.getElementById('confirm-yes-btn');
  const confirmNoBtn = document.getElementById('confirm-no-btn');
  const statusIndicator = document.getElementById('status-indicator');
  const queueBtn = document.getElementById('queue-btn');
  const queueMenu = document.getElementById('queue-menu');
  const projectNameEl = document.getElementById('project-name');
  const agentModelsEl = document.getElementById('agent-models');
  const configToggleBtn = document.getElementById('config-toggle-btn');
  const configPanel = document.getElementById('config-panel');
  const configGrid = document.getElementById('config-grid');
  const configStatus = document.getElementById('config-status');
  const globalConfigCheckbox = document.getElementById('global-config-checkbox');
  const sessionList = document.getElementById('session-list');
  const newSessionBtn = document.getElementById('new-session-btn');
  const newSessionForm = document.getElementById('new-session-form');
  const newSessionName = document.getElementById('new-session-name');
  const newSessionProject = document.getElementById('new-session-project');
  const cancelSessionBtn = document.getElementById('cancel-session-btn');
  const editOverlay = document.getElementById('edit-overlay');
  const editTextarea = document.getElementById('edit-textarea');
  const editError = document.getElementById('edit-error');
  const editSaveBtn = document.getElementById('edit-save-btn');
  const editCancelBtn = document.getElementById('edit-cancel-btn');
  const helpBtn = document.getElementById('help-btn');
  const helpOverlay = document.getElementById('help-overlay');
  const helpCloseBtn = document.getElementById('help-close-btn');

  let userScrolledUp = false;
  let isRenderingChat = false;
  let ws = null;
  let wsSessionId = null;
  let latestAgents = {};
  let latestGlobalAgents = {};
  let sessions = [];
  let activeSessionId = null;
  let editState = null;

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

  function sessionApi(path) {
    if (!activeSessionId) throw new Error('no active session');
    return `/api/sessions/${encodeURIComponent(activeSessionId)}${path}`;
  }

  function activeSession() {
    return sessions.find((s) => s.id === activeSessionId) || null;
  }

  chatArea.addEventListener('scroll', () => {
    if (isRenderingChat) return;
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

  function parseChatMD(text) {
    const turns = [];
    const TURN_HEADER_RE = /^##\s+\[@(\w+)\]\s+(.+)$/;
    const lines = text.split('\n');
    let current = null;
    let preamble = '';

    function finishCurrent() {
      if (!current) return;
      current.body = current.body.replace(/\n?---\s*$/, '');
      turns.push(current);
      current = null;
    }

    for (const line of lines) {
      const m = TURN_HEADER_RE.exec(line);
      if (m) {
        if (!current && turns.length === 0 && preamble.trim()) {
          turns.push({ author: 'system', time: 'compacted summary', body: preamble.trim() });
          preamble = '';
        }
        finishCurrent();
        current = { author: m[1], time: m[2], body: '' };
      } else if (current) {
        if (current.body) current.body += '\n';
        current.body += line;
      } else {
        if (preamble) preamble += '\n';
        preamble += line;
      }
    }
    finishCurrent();
    if (turns.length === 0 && preamble.trim()) {
      turns.push({ author: 'system', time: 'compacted summary', body: preamble.trim() });
    }
    return turns;
  }

  function formatTokenCount(n) {
    if (!n || n <= 0) return null;
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
    return String(n);
  }

  function renderTurns(turns, tokenData) {
    const previousScrollHeight = chatArea.scrollHeight;
    const previousScrollTop = chatArea.scrollTop;
    const previousDistanceFromBottom = previousScrollHeight - previousScrollTop - chatArea.clientHeight;
    const wasAtBottom = previousDistanceFromBottom < 60;
    isRenderingChat = true;

    chatInner.innerHTML = '';
    turns.forEach((turn, turnIdx) => {
      const card = document.createElement('div');
      card.className = 'turn-card';

      const header = document.createElement('div');
      header.className = 'turn-header';

      const avatar = document.createElement(turn.author === 'claude' || turn.author === 'codex' || turn.author === 'deepseek' ? 'img' : 'span');
      avatar.className = `turn-avatar ${turn.author}`;
      if (avatar.tagName === 'IMG') {
        avatar.src = `/icons/${turn.author}.png`;
        avatar.alt = turn.author;
      } else {
        avatar.textContent = turn.author === 'you' ? 'Y' : turn.author[0].toUpperCase();
      }

      const authorSpan = document.createElement('span');
      authorSpan.className = `turn-author ${turn.author}`;
      authorSpan.textContent = `@${turn.author}`;

      const timeSpan = document.createElement('span');
      timeSpan.className = 'turn-time';
      timeSpan.textContent = turn.time;

      header.appendChild(avatar);
      header.appendChild(authorSpan);
      header.appendChild(timeSpan);

      const td = tokenData && tokenData[turnIdx];
      if (td) {
        const inTok = formatTokenCount(td.prompt_tokens_est);
        const outTok = formatTokenCount(td.response_tokens_est);
        if (inTok || outTok) {
          const badge = document.createElement('span');
          badge.className = 'turn-token-badge';
          const parts = [];
          if (inTok) parts.push('~' + inTok + ' in');
          if (outTok) parts.push('~' + outTok + ' out');
          badge.textContent = parts.join(' / ');
          if (td.token_usage && td.token_usage.total_tokens) {
            badge.title = 'prompt=' + (td.token_usage.prompt_tokens || '?') +
              ' completion=' + (td.token_usage.completion_tokens || '?') +
              ' total=' + td.token_usage.total_tokens;
          } else {
            badge.title = 'char/4 estimate';
          }
          header.appendChild(badge);
        }
      }

      if (turn.author === 'you') {
        const editBtn = document.createElement('button');
        editBtn.className = 'turn-edit-btn';
        editBtn.textContent = '\u270E';
        editBtn.title = 'Edit this message';
        editBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          startEditTurn(card, body, turnIdx, turn.body);
        });
        header.appendChild(editBtn);
      }

      const eraseBtn = document.createElement('button');
      eraseBtn.className = 'turn-erase-btn';
      eraseBtn.textContent = '\u00D7';
      eraseBtn.title = 'Erase this message';
      eraseBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        eraseTurn(turnIdx);
      });
      header.appendChild(eraseBtn);

      const body = document.createElement('div');
      body.className = 'turn-body';
      if (turn.body.trim()) {
        body.innerHTML = marked.parse(turn.body.trim());
        colorMentions(body);
        linkFiles(body);
      }

      card.appendChild(header);
      card.appendChild(body);
      chatInner.appendChild(card);
    });

    if (wasAtBottom) {
      userScrolledUp = false;
      chatArea.scrollTop = chatArea.scrollHeight;
      newMsgsBtn.classList.add('hidden');
    } else {
      userScrolledUp = true;
      chatArea.scrollTop = Math.max(0, chatArea.scrollHeight - previousDistanceFromBottom - chatArea.clientHeight);
      newMsgsBtn.classList.remove('hidden');
    }

    requestAnimationFrame(() => {
      isRenderingChat = false;
    });
  }

  function colorMentions(container) {
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null, false);
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);
    const mentionRE = /@(claude|codex|deepseek)\b/gi;
    for (const node of textNodes) {
      if (node.parentNode && node.parentNode.closest('pre, code, .mention')) continue;
      const text = node.textContent;
      if (!mentionRE.test(text)) continue;
      mentionRE.lastIndex = 0;
      const frag = document.createDocumentFragment();
      let lastIdx = 0;
      let m;
      while ((m = mentionRE.exec(text)) !== null) {
        if (m.index > lastIdx) frag.appendChild(document.createTextNode(text.slice(lastIdx, m.index)));
        const span = document.createElement('span');
        span.className = `mention ${m[1].toLowerCase()}`;
        span.textContent = m[0];
        frag.appendChild(span);
        lastIdx = mentionRE.lastIndex;
      }
      if (lastIdx < text.length) frag.appendChild(document.createTextNode(text.slice(lastIdx)));
      node.parentNode.replaceChild(frag, node);
    }
  }

  const FILE_PATH_RE = /(?:^|[\s(])((?:(?:[a-zA-Z]:[\\/]|\/|\.\.?[\\/])?[\w.\-\\/]+[\\/][\w.\-\\/]*\.[a-zA-Z]{1,8}))(?=[\s,;:.)'"\]>]|$)/g;

  function linkFiles(container) {
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null, false);
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);
    for (const node of textNodes) {
      const parent = node.parentNode;
      if (!parent) continue;
      if (parent.closest('pre, code, a, .file-link, .mention, .turn-erase-btn, button, input, textarea, select')) continue;
      const text = node.textContent;
      if (!/[\\/]/.test(text) || !/\.[a-zA-Z]{1,8}/.test(text)) continue;
      FILE_PATH_RE.lastIndex = 0;
      const frag = document.createDocumentFragment();
      let lastIdx = 0;
      let m;
      while ((m = FILE_PATH_RE.exec(text)) !== null) {
        const fullMatch = m[0];
        const pathStart = fullMatch.match(/^\s/) ? m.index + 1 : m.index;
        const pathLen = fullMatch.trimStart().length;
        if (pathStart > lastIdx) frag.appendChild(document.createTextNode(text.slice(lastIdx, pathStart)));
        const link = document.createElement('span');
        link.className = 'file-link';
        link.textContent = text.slice(pathStart, pathStart + pathLen);
        link.title = 'Click to open';
        link.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          showFileMenu(link, text.slice(pathStart, pathStart + pathLen), e);
        });
        frag.appendChild(link);
        lastIdx = pathStart + pathLen;
      }
      if (lastIdx < text.length) frag.appendChild(document.createTextNode(text.slice(lastIdx)));
      if (frag.childNodes.length > 1 || (frag.firstChild && frag.firstChild !== node)) {
        node.parentNode.replaceChild(frag, node);
      }
    }
  }

  let fileMenuEl = null;

  function hideFileMenu() {
    if (fileMenuEl) {
      fileMenuEl.remove();
      fileMenuEl = null;
    }
  }

  function showFileMenu(anchor, filePath, event) {
    hideFileMenu();
    const menu = document.createElement('div');
    menu.className = 'file-menu';
    menu.innerHTML = `
      <button class="file-menu-item" data-action="open">Open file</button>
      <button class="file-menu-item" data-action="explorer">Open in explorer</button>
    `;
    menu.querySelector('[data-action="open"]').addEventListener('click', async () => {
      hideFileMenu();
      try {
        await fetch(sessionApi('/open-file'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: filePath }),
        });
      } catch (e) {
        console.error('Open file failed:', e);
      }
    });
    menu.querySelector('[data-action="explorer"]').addEventListener('click', async () => {
      hideFileMenu();
      try {
        await fetch(sessionApi('/open-explorer'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: filePath }),
        });
      } catch (e) {
        console.error('Open explorer failed:', e);
      }
    });
    document.body.appendChild(menu);
    const rect = anchor.getBoundingClientRect();
    menu.style.left = rect.left + 'px';
    menu.style.top = (rect.bottom + 4) + 'px';
    if (rect.bottom + menu.offsetHeight + 8 > window.innerHeight) {
      menu.style.top = (rect.top - menu.offsetHeight - 4) + 'px';
    }
    fileMenuEl = menu;
    setTimeout(() => {
      document.addEventListener('click', hideFileMenu, { once: true });
    }, 0);
  }

  function projectShortName(projectRoot) {
    if (!projectRoot) return '';
    const parts = projectRoot.replaceAll('\\', '/').split('/').filter(Boolean);
    return parts[parts.length - 1] || projectRoot;
  }

  function renderSessions() {
    sessionList.innerHTML = '';
    for (const session of sessions) {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'session-item' + (session.id === activeSessionId ? ' active' : '');
      item.dataset.sessionId = session.id;

      const text = document.createElement('span');
      text.className = 'session-text';
      const name = document.createElement('span');
      name.className = 'session-name';
      name.textContent = session.name || session.id;
      name.title = 'Double-click to rename';
      name.addEventListener('click', (e) => e.stopPropagation());
      name.addEventListener('dblclick', (e) => {
        e.preventDefault();
        e.stopPropagation();
        startRenameSession(session.id, name);
      });
      const project = document.createElement('span');
      project.className = 'session-project';
      project.textContent = projectShortName(session.project_root);
      project.title = session.project_root || '';
      text.appendChild(name);
      text.appendChild(project);

      const del = document.createElement('span');
      del.className = 'session-delete';
      del.textContent = 'x';
      del.title = 'Delete session';
      del.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        deleteSession(session.id);
      });

      item.appendChild(text);
      item.appendChild(del);
      item.addEventListener('click', () => switchSession(session.id));
      sessionList.appendChild(item);
    }
  }

  function startRenameSession(sessionId, nameEl) {
    const session = sessions.find((s) => s.id === sessionId);
    if (!session) return;
    const currentName = session.name || session.id;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'session-rename-input';
    input.value = currentName;
    input.style.width = '100%';
    input.style.boxSizing = 'border-box';
    nameEl.replaceWith(input);
    input.focus();
    input.setSelectionRange(0, input.value.length);

    const finish = async () => {
      const newName = input.value.trim();
      if (newName && newName !== currentName) {
        await renameSession(sessionId, newName);
      } else {
        input.replaceWith(nameEl);
      }
    };

    input.addEventListener('blur', finish);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      if (e.key === 'Escape') { input.value = currentName; input.blur(); }
    });
  }

  async function loadSessions() {
    const res = await fetch('/api/sessions');
    const data = await res.json();
    sessions = data.sessions || [];
    activeSessionId = data.active_session_id || (sessions[0] && sessions[0].id) || null;
    renderSessions();
    return activeSessionId;
  }

  async function switchSession(sessionId) {
    if (!sessionId || sessionId === activeSessionId) return;
    await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/activate`, { method: 'POST' });
    activeSessionId = sessionId;
    userScrolledUp = false;
    renderSessions();
    connectWS();
    await Promise.all([fetchChat(), fetchStatus(), fetchTrace()]);
  }

  async function createSession(name, projectRoot) {
    const res = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, project_root: projectRoot }),
    });
    if (!res.ok) {
      const err = await res.json();
      configStatus.textContent = err.error || 'session create failed';
      return;
    }
    const data = await res.json();
    await loadSessions();
    activeSessionId = data.active_session_id;
    renderSessions();
    connectWS();
    await Promise.all([fetchChat(), fetchStatus(), fetchTrace()]);
  }

  async function deleteSession(sessionId) {
    const session = sessions.find((s) => s.id === sessionId);
    const label = session ? session.name : sessionId;
    if (!window.confirm(`Delete session "${label}"?`)) return;
    const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
    if (!res.ok) {
      const err = await res.json();
      window.alert(err.error || 'delete failed');
      return;
    }
    const data = await res.json();
    sessions = data.sessions || [];
    activeSessionId = data.active_session_id || (sessions[0] && sessions[0].id) || null;
    renderSessions();
    connectWS();
    await Promise.all([fetchChat(), fetchStatus(), fetchTrace()]);
  }

  async function renameSession(sessionId, newName) {
    if (!newName || !newName.trim()) return;
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName.trim() }),
      });
      if (res.ok) {
        await loadSessions();
        renderSessions();
        await fetchStatus();
      }
    } catch (e) {
      console.error('Rename failed:', e);
    }
  }

  async function fetchChat() {
    if (!activeSessionId) return;
    const targetSessionId = activeSessionId;
    try {
      const [chatRes, eventsRes] = await Promise.all([
        fetch(`/api/sessions/${encodeURIComponent(targetSessionId)}/chat`),
        fetch(`/api/sessions/${encodeURIComponent(targetSessionId)}/events`),
      ]);
      if (targetSessionId !== activeSessionId) return;
      const chatData = await chatRes.json();
      const turns = parseChatMD(chatData.text || '');
      let tokenData = null;
      if (eventsRes.ok) {
        const eventsData = await eventsRes.json();
        const td = {};
        eventsData.turns.forEach((evt, i) => {
          if (evt.prompt_tokens_est || evt.response_tokens_est) {
            td[i] = evt;
          }
        });
        if (Object.keys(td).length > 0) tokenData = td;
      }
      renderTurns(turns, tokenData);
    } catch (e) {
      console.error('Failed to fetch chat:', e);
    }
  }

  async function sendMessage() {
    if (!activeSessionId) return;
    const text = msgInput.value.trim();
    if (!text) return;

    if (text.startsWith('/')) {
      const cmdName = text.split(/\s+/)[0].slice(1).toLowerCase();
      const cmdDef = COMMANDS.find(c => c.name === cmdName);
      if (cmdDef) {
        msgInput.value = '';
        cmdDef.action();
        return;
      }
    }

    msgInput.value = '';
    try {
      const res = await fetch(sessionApi('/send'), {
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

  function showConfirm(title, msg, onYes) {
    confirmTitle.textContent = title;
    confirmMsg.textContent = msg;
    confirmYesBtn.onclick = async () => {
      confirmOverlay.classList.add('hidden');
      confirmYesBtn.onclick = null;
      await onYes();
    };
    confirmNoBtn.onclick = () => {
      confirmOverlay.classList.add('hidden');
    };
    confirmOverlay.classList.remove('hidden');
    confirmNoBtn.focus();
  }

  async function compactChat() {
    if (!activeSessionId) return;
    showConfirm(
      'Compact chat?',
      'This will summarize the chat log using Deepseek Flash. Older turns will be replaced with a summary. This cannot be undone.',
      async () => {
        try {
          await fetch(sessionApi('/compact'), { method: 'POST' });
        } catch (e) {
          console.error('Compact failed:', e);
        }
      }
    );
  }

  async function eraseChat() {
    if (!activeSessionId) return;
    showConfirm(
      'Clear chat?',
      'This will permanently delete all chat turns for this session. This cannot be undone.',
      async () => {
        try {
          await fetch(sessionApi('/erase'), { method: 'POST' });
          await fetchChat();
        } catch (e) {
          console.error('Clear failed:', e);
        }
      }
    );
  }

  async function startEditTurn(card, body, turnIdx, originalText) {
    if (isRenderingChat) return;
    editState = { turnIdx };
    editError.textContent = '';
    editTextarea.value = originalText;
    editOverlay.classList.remove('hidden');
    editTextarea.focus();
    editTextarea.setSelectionRange(editTextarea.value.length, editTextarea.value.length);
  }

  function cancelEditTurn() {
    editState = null;
    editOverlay.classList.add('hidden');
    editError.textContent = '';
  }

  async function saveEditTurn() {
    if (!activeSessionId || !editState) return;
    const turnIdx = editState.turnIdx;
    const newText = editTextarea.value;
    editSaveBtn.disabled = true;
    editError.textContent = '';
    try {
      const res = await fetch(sessionApi('/edit_turn'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index: turnIdx, text: newText }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        editError.textContent = err.error || res.statusText || 'Edit failed';
        return;
      }
      cancelEditTurn();
      await fetchChat();
    } catch (e) {
      console.error('Edit failed:', e);
      editError.textContent = 'Edit failed';
    } finally {
      editSaveBtn.disabled = false;
    }
  }

  async function eraseTurn(turnIdx) {
    if (!activeSessionId) return;
    showConfirm(
      'Erase message?',
      'Delete this message permanently?',
      async () => {
        try {
          await fetch(sessionApi('/erase_turn'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ index: turnIdx }),
          });
          await fetchChat();
        } catch (e) {
          console.error('Erase turn failed:', e);
        }
      }
    );
  }

  async function restartServer() {
    if (!activeSessionId) return;
    showConfirm(
      'Restart Council?',
      'The server will shut down and restart. The page will reload once the server is back.',
      async () => {
        try {
          await fetch(sessionApi('/restart'), { method: 'POST' });
          let attempts = 0;
          const poll = setInterval(async () => {
            attempts++;
            try {
              const res = await fetch('/api/sessions');
              if (res.ok) {
                clearInterval(poll);
                window.location.reload();
              }
            } catch (_) {}
            if (attempts > 60) {
              clearInterval(poll);
              window.location.reload();
            }
          }, 1000);
        } catch (e) {
          console.error('Restart failed:', e);
        }
      }
    );
  }

  async function cancelDispatch() {
    if (!activeSessionId) return;
    try {
      await fetch(sessionApi('/cancel'), { method: 'POST' });
    } catch (e) {
      console.error('Cancel failed:', e);
    }
  }

  async function cancelDispatchRequest(requestId) {
    if (!activeSessionId || !requestId) return;
    try {
      const res = await fetch(sessionApi('/dispatch/' + encodeURIComponent(requestId) + '/cancel'), {
        method: 'POST',
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        console.error('Cancel dispatch request failed:', err.error || res.statusText);
      }
      await fetchStatus();
    } catch (e) {
      console.error('Cancel dispatch request failed:', e);
    }
  }

  async function fetchStatus() {
    if (!activeSessionId) return;
    const targetSessionId = activeSessionId;
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(targetSessionId)}/status`);
      const data = await res.json();
      if (targetSessionId !== activeSessionId) return;
      updateStatus(data);
    } catch (_) {}
  }

  async function patchConfig(changes) {
    if (!activeSessionId && !globalConfigCheckbox.checked) return;
    configStatus.textContent = 'saving...';
    const url = globalConfigCheckbox.checked ? '/api/config' : sessionApi('/config');
    try {
      const res = await fetch(url, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changes),
      });
      if (!res.ok) {
        const err = await res.json();
        configStatus.textContent = err.error || 'save failed';
        return;
      }
      await fetchStatus();
      configStatus.textContent = 'saved';
      setTimeout(() => {
        if (configStatus.textContent === 'saved') configStatus.textContent = '';
      }, 1200);
    } catch (_) {
      configStatus.textContent = 'save failed';
    }
  }

  async function fetchTrace() {
    if (!activeSessionId) return;
    const targetSessionId = activeSessionId;
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(targetSessionId)}/trace`);
      const data = await res.json();
      if (targetSessionId !== activeSessionId) return;
      renderTrace(data.events || []);
    } catch (_) {}
  }

  function renderTrace(events) {
    traceList.innerHTML = '';
    for (const event of events) {
      appendTraceEvent(event);
    }
  }

  function cleanTraceText(text) {
    return String(text || '')
      .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '\n')
      .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, '');
  }

  function appendTraceEvent(event) {
    const item = document.createElement('div');
    item.className = 'trace-item';

    const agent = event.agent || 'system';
    const meta = document.createElement('div');
    meta.className = 'trace-meta';
    meta.textContent = `${event.time || ''} `;
    const agentSpan = document.createElement('span');
    agentSpan.className = `trace-agent ${agent}`;
    agentSpan.textContent = `@${agent}`;
    meta.appendChild(agentSpan);

    const message = document.createElement('div');
    message.className = 'trace-message';
    message.textContent = cleanTraceText(event.message);

    item.appendChild(meta);
    item.appendChild(message);

    if (event.detail) {
      const detail = document.createElement('div');
      detail.className = 'trace-detail';
      detail.textContent = cleanTraceText(event.detail);
      item.appendChild(detail);
    }

    traceList.appendChild(item);
    while (traceList.children.length > 100) {
      traceList.removeChild(traceList.firstChild);
    }
    traceList.scrollTop = traceList.scrollHeight;
  }

  function connectWS() {
    if (!activeSessionId) return;
    if (ws) {
      ws.onclose = null;
      ws.close();
    }
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const targetSessionId = activeSessionId;
    const url = `${protocol}://${window.location.host}/ws/${encodeURIComponent(targetSessionId)}`;

    wsSessionId = targetSessionId;
    ws = new WebSocket(url);

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.session_id && msg.session_id !== activeSessionId) return;
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
      if (wsSessionId === activeSessionId) {
        setTimeout(connectWS, 2000);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }

  function updateStatus(data) {
    const session = data.session || activeSession();
    if (session) {
      projectNameEl.textContent = `${session.name || session.id} - ${projectShortName(session.project_root)}`;
    } else {
      projectNameEl.textContent = data.project || '';
    }
    latestAgents = data.agents || {};
    latestGlobalAgents = data.global_agents || latestGlobalAgents || {};
    renderAgentModels(latestAgents);
    renderConfig(globalConfigCheckbox.checked ? latestGlobalAgents : latestAgents);
    renderQueueStatus(data);

    if (data.busy) {
      statusIndicator.className = 'busy';
      const agentName = data.agent || '?';
      if (agentName === 'summarizer') {
        statusIndicator.innerHTML = '&#9679; compacting&hellip;';
      } else {
        statusIndicator.innerHTML = '&#9679; @' + agentName + ' thinking&hellip;';
      }
      cancelBtn.classList.remove('hidden');
    } else {
      statusIndicator.className = 'idle';
      statusIndicator.innerHTML = '&#9679; idle';
      cancelBtn.classList.add('hidden');
    }
  }

  function dispatchLabel(item) {
    if (!item || !item.agent) return '@?';
    const source = item.source === 'user' ? 'from you' : 'from agent';
    return '@' + item.agent + ' ' + source;
  }

  function renderQueueStatus(data) {
    const current = data.current_dispatch || null;
    const queue = data.dispatch_queue || [];
    const total = (current ? 1 : 0) + queue.length;
    if (!total) {
      queueBtn.classList.add('hidden');
      queueMenu.classList.add('hidden');
      queueMenu.innerHTML = '';
      return;
    }
    queueBtn.classList.remove('hidden');
    queueBtn.textContent = queue.length ? `Queue ${total}` : 'Queue 1';
    queueMenu.innerHTML = '';

    if (current) {
      queueMenu.appendChild(makeQueueItem(current, 'Running'));
    }
    queue.forEach((item, idx) => {
      queueMenu.appendChild(makeQueueItem(item, `Queued ${idx + 1}`));
    });
  }

  function makeQueueItem(item, stateLabel) {
    const row = document.createElement('div');
    row.className = 'queue-item';

    const text = document.createElement('div');
    text.className = 'queue-item-text';
    const state = document.createElement('span');
    state.className = 'queue-item-state';
    state.textContent = stateLabel;
    const label = document.createElement('span');
    label.className = 'queue-item-label';
    label.textContent = dispatchLabel(item);
    text.appendChild(state);
    text.appendChild(label);

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'queue-cancel-btn';
    btn.textContent = 'Cancel';
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      cancelDispatchRequest(item.id);
    });

    row.appendChild(text);
    row.appendChild(btn);
    return row;
  }

  function renderAgentModels(agents) {
    const order = ['claude', 'codex', 'deepseek'];
    agentModelsEl.innerHTML = '';
    for (const id of order) {
      const info = agents[id];
      if (!info) continue;
      const pill = document.createElement('span');
      pill.className = `agent-model ${id}`;
      pill.title = `${info.runtime || ''} - ${info.note || ''}`.trim();
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

  const acBox = document.createElement('div');
  acBox.id = 'autocomplete-box';
  acBox.className = 'hidden';
  document.body.appendChild(acBox);

  const COMMANDS = [
    { name: 'compact', description: 'Summarize chat log', action: compactChat },
    { name: 'erase', description: 'Erase all chat turns', action: eraseChat },
    { name: 'clear', description: 'Clear all chat turns', action: eraseChat },
    { name: 'restart', description: 'Restart Council server', action: restartServer },
  ];

  let acState = {
    open: false,
    items: [],
    selectedIdx: 0,
    triggerStart: -1,
    triggerChar: '',
    query: '',
  };

  function closeAC() {
    acState.open = false;
    acState.items = [];
    acState.selectedIdx = 0;
    acState.triggerStart = -1;
    acState.triggerChar = '';
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
      kind.textContent = it.kind === 'command' ? 'cmd' : (it.kind === 'agent' ? 'agent' : 'file');
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
    if (!activeSessionId) return { agents: [], files: [] };
    try {
      const res = await fetch(sessionApi('/completions') + '?q=' + encodeURIComponent(query));
      return await res.json();
    } catch (_) {
      return { agents: [], files: [] };
    }
  }

  function buildItems(data) {
    const items = [];
    for (const a of (data.agents || [])) {
      items.push({ label: a, insert: a, kind: 'agent' });
    }
    for (const f of (data.files || [])) {
      items.push({ label: f, insert: f, kind: 'file' });
    }
    return items.slice(0, 40);
  }

  function buildCommandItems(query) {
    const q = query.toLowerCase();
    return COMMANDS
      .filter(c => c.name.startsWith(q))
      .map(c => ({ label: '/' + c.name + ' - ' + c.description, insert: c.name, kind: 'command', description: c.description }));
  }

  async function refreshAC() {
    if (!acState.open) return;
    if (acState.triggerChar === '/') {
      acState.items = buildCommandItems(acState.query);
    } else {
      const data = await fetchCompletions(acState.query);
      acState.items = buildItems(data);
    }
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
    const inserted = acState.triggerChar + it.insert + ' ';
    msgInput.value = before + inserted + after;
    const caret = (before + inserted).length;
    msgInput.setSelectionRange(caret, caret);
    closeAC();
    msgInput.focus();
  }

  function updateACFromInput() {
    const val = msgInput.value;
    const caret = msgInput.selectionStart;
    let i = caret - 1;
    let trigger = -1;
    let triggerCh = '';
    while (i >= 0) {
      const ch = val[i];
      if (ch === '@' || ch === '/') {
        if (i === 0 || /\s/.test(val[i - 1])) {
          trigger = i;
          triggerCh = ch;
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
    acState.triggerChar = triggerCh;
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
    setTimeout(closeAC, 150);
  });

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
  eraseBtn.addEventListener('click', eraseChat);
  cancelBtn.addEventListener('click', cancelDispatch);
  queueBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    queueMenu.classList.toggle('hidden');
  });
  document.addEventListener('click', (e) => {
    if (!queueMenu.classList.contains('hidden') && !e.target.closest('#queue-menu-wrap')) {
      queueMenu.classList.add('hidden');
    }
  });
  editSaveBtn.addEventListener('click', saveEditTurn);
  editCancelBtn.addEventListener('click', cancelEditTurn);
  editOverlay.addEventListener('click', (e) => {
    if (e.target === editOverlay) cancelEditTurn();
  });
  editTextarea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      saveEditTurn();
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      cancelEditTurn();
    }
  });
  helpBtn.addEventListener('click', () => {
    helpOverlay.classList.remove('hidden');
  });
  helpCloseBtn.addEventListener('click', () => {
    helpOverlay.classList.add('hidden');
  });
  helpOverlay.addEventListener('click', (e) => {
    if (e.target === helpOverlay) helpOverlay.classList.add('hidden');
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !helpOverlay.classList.contains('hidden')) {
      helpOverlay.classList.add('hidden');
    }
  });
  configToggleBtn.addEventListener('click', () => {
    configPanel.classList.toggle('hidden');
    renderConfig(globalConfigCheckbox.checked ? latestGlobalAgents : latestAgents);
  });
  globalConfigCheckbox.addEventListener('change', () => {
    renderConfig(globalConfigCheckbox.checked ? latestGlobalAgents : latestAgents);
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

  newSessionBtn.addEventListener('click', () => {
    const current = activeSession();
    newSessionProject.value = current ? current.project_root : '';
    newSessionName.value = '';
    newSessionForm.classList.remove('hidden');
    newSessionName.focus();
  });
  cancelSessionBtn.addEventListener('click', () => {
    newSessionForm.classList.add('hidden');
  });
  newSessionForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    await createSession(newSessionName.value.trim() || 'untitled', newSessionProject.value.trim());
    newSessionForm.classList.add('hidden');
  });

  async function init() {
    await loadSessions();
    renderSessions();
    connectWS();
    await Promise.all([fetchChat(), fetchStatus(), fetchTrace()]);
  }

  init();
})();
