// Council App - Multi-session frontend logic

(() => {
  const chatInner = document.getElementById('chat-inner');
  const chatArea = document.getElementById('chat-area');
  const traceList = document.getElementById('trace-list');
  const msgInput = document.getElementById('msg-input');
  const msgHighlights = document.getElementById('msg-highlights');
  const sendNowBtn = document.getElementById('send-now-btn');
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
  const compactStatusEl = document.getElementById('compact-status');
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
  let latestDispatch = {};
  let latestGlobalDispatch = {};
  let latestStatus = null;
  let latestAgentsMd = null;
  let latestTraceEvents = [];
  let latestTurns = [];
  let sessions = [];
  let activeSessionId = null;
  let editState = null;
  let openTurnMenu = null;
  const AGENT_IDS = ['claude', 'codex', 'deepseek', 'hermes'];
  const ATTACHMENT_POLICY_OPTIONS = [
    ['path-visible', 'Path visible'],
    ['placeholder', 'Placeholder only'],
  ];

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

    function preambleTurn(body) {
      const trimmed = body.trim();
      const firstLine = trimmed.split('\n', 1)[0] || '';
      const m = /^compacted\s+(\S+)/i.exec(firstLine);
      return {
        author: 'system',
        time: m ? `compacted ${m[1]}` : 'compacted summary',
        body: trimmed,
      };
    }

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
          turns.push(preambleTurn(preamble));
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
      turns.push(preambleTurn(preamble));
    }
    return turns;
  }

  function formatTokenCount(n) {
    if (!n || n <= 0) return null;
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
    return String(n);
  }

  function displayTurnTime(time) {
    const raw = String(time || '').trim();
    const compacted = /^compacted\s+(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})$/i.exec(raw);
    if (compacted) {
      return `${compacted[1]}-${compacted[2]}-${compacted[3]} ${compacted[4]}:${compacted[5]}:${compacted[6]}`;
    }
    return raw;
  }

  function turnEventKey(author, time) {
    return `${author || 'system'}\n${time || ''}`;
  }

  function renderTurns(turns, tokenData) {
    const previousScrollHeight = chatArea.scrollHeight;
    const previousScrollTop = chatArea.scrollTop;
    const previousDistanceFromBottom = previousScrollHeight - previousScrollTop - chatArea.clientHeight;
    const wasAtBottom = previousDistanceFromBottom < 60;
    isRenderingChat = true;

    chatInner.innerHTML = '';
    const tokenQueues = tokenData && tokenData.byKey ? tokenData.byKey : null;
    const tokenQueueOffsets = {};
    turns.forEach((turn, turnIdx) => {
      const card = document.createElement('div');
      card.className = 'turn-card';

      const header = document.createElement('div');
      header.className = 'turn-header';

      const hasAgentIcon = ['claude', 'codex', 'deepseek'].includes(turn.author);
      const avatar = document.createElement(hasAgentIcon ? 'img' : 'span');
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
      timeSpan.textContent = displayTurnTime(turn.time);
      if (timeSpan.textContent !== turn.time) {
        timeSpan.title = turn.time;
      }

      header.appendChild(avatar);
      header.appendChild(authorSpan);
      header.appendChild(timeSpan);

      let td = tokenData && tokenData[turnIdx];
      if (tokenQueues) {
        const key = turnEventKey(turn.author, turn.time);
        const queue = tokenQueues[key] || [];
        const offset = tokenQueueOffsets[key] || 0;
        td = queue[offset] || td;
        tokenQueueOffsets[key] = offset + 1;
      }
      if (td && td.metadata && td.metadata.dispatch_mode) {
        const modeBadge = document.createElement('span');
        modeBadge.className = `turn-mode-badge ${td.metadata.dispatch_mode}`;
        modeBadge.textContent = td.metadata.dispatch_mode === 'queued' ? 'Queued' : 'Live';
        modeBadge.title = td.metadata.dispatch_mode === 'queued'
          ? 'Mentioned agents were queued behind earlier work.'
          : 'Mentioned agents were started immediately when possible.';
        header.appendChild(modeBadge);
      }
      if (td && td.metadata && td.metadata.dispatch_status && td.metadata.dispatch_status !== 'completed') {
        const statusBadge = document.createElement('span');
        statusBadge.className = `turn-status-badge ${td.metadata.dispatch_status}`;
        statusBadge.textContent = td.metadata.dispatch_status === 'running'
          ? 'Running'
          : td.metadata.dispatch_status === 'cancelled'
            ? 'Cancelled'
            : 'Failed';
        statusBadge.title = 'Reserved agent chat slot status.';
        header.appendChild(statusBadge);
      }
      if (td && turn.author !== 'you') {
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
          startEditTurn(turn, turnIdx);
        });
        header.appendChild(editBtn);
      }

      const moreWrap = document.createElement('div');
      moreWrap.className = 'turn-more-wrap';
      const moreBtn = document.createElement('button');
      moreBtn.className = 'turn-more-btn';
      moreBtn.textContent = '\u22EF';
      moreBtn.title = 'More message options';
      moreBtn.setAttribute('aria-label', 'More message options');
      const moreMenu = document.createElement('div');
      moreMenu.className = 'turn-more-menu hidden';
      const copyMetadataBtn = document.createElement('button');
      copyMetadataBtn.type = 'button';
      copyMetadataBtn.textContent = 'Copy chat metadata';
      copyMetadataBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        await copyTurnMetadata(turn, turnIdx, td, turns.length);
        copyMetadataBtn.textContent = 'Copied';
        setTimeout(() => {
          copyMetadataBtn.textContent = 'Copy chat metadata';
        }, 1200);
        moreMenu.classList.add('hidden');
        openTurnMenu = null;
      });
      moreMenu.appendChild(copyMetadataBtn);
      moreBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (openTurnMenu && openTurnMenu !== moreMenu) {
          openTurnMenu.classList.add('hidden');
        }
        moreMenu.classList.toggle('hidden');
        openTurnMenu = moreMenu.classList.contains('hidden') ? null : moreMenu;
      });
      moreWrap.appendChild(moreBtn);
      moreWrap.appendChild(moreMenu);
      header.appendChild(moreWrap);

      const eraseBtn = document.createElement('button');
      eraseBtn.className = 'turn-erase-btn';
      eraseBtn.textContent = '\u00D7';
      eraseBtn.title = 'Erase this message';
      eraseBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        eraseTurn(turn, turnIdx);
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
      if (td && td.tool_calls && td.tool_calls.length) {
        card.appendChild(renderToolProvenance(td.tool_calls));
      }
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

  function renderToolProvenance(toolCalls) {
    const wrap = document.createElement('details');
    wrap.className = 'turn-tools';

    const summary = document.createElement('summary');
    summary.className = 'turn-tools-summary';
    const count = document.createElement('span');
    count.className = 'turn-tools-count';
    count.textContent = String(toolCalls.length);
    summary.appendChild(count);
    summary.appendChild(document.createTextNode(' tool ' + (toolCalls.length === 1 ? 'action' : 'actions')));
    wrap.appendChild(summary);

    const list = document.createElement('div');
    list.className = 'turn-tools-list';
    for (const item of toolCalls) {
      const chip = document.createElement('div');
      chip.className = 'turn-tool-chip ' + cleanToolKind(item.kind);
      const label = document.createElement('span');
      label.className = 'turn-tool-label';
      label.textContent = item.label || item.detail || 'tool action';
      chip.appendChild(label);
      if (item.detail) {
        chip.title = item.detail;
      }
      const paths = Array.isArray(item.paths)
        ? item.paths.filter((path) => !String(label.textContent || '').includes(String(path || '')))
        : [];
      const allPaths = Array.isArray(item.paths)
        ? item.paths.map((path) => String(path || '').trim()).filter(Boolean)
        : [];
      const primaryPath = allPaths[0] || '';
      if (primaryPath) {
        chip.classList.add('has-path');
        chip.tabIndex = 0;
        chip.setAttribute('role', 'button');
        chip.setAttribute('aria-label', 'File actions for ' + primaryPath);
        chip.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          showFileMenu(chip, primaryPath, e);
        });
        chip.addEventListener('keydown', (e) => {
          if (e.key !== 'Enter' && e.key !== ' ') return;
          e.preventDefault();
          e.stopPropagation();
          showFileMenu(chip, primaryPath, e);
        });
      }
      if (paths.length) {
        const pathText = document.createElement('span');
        pathText.className = 'turn-tool-paths';
        pathText.textContent = paths.slice(0, 3).join(', ');
        chip.appendChild(pathText);
      }
      list.appendChild(chip);
    }
    wrap.appendChild(list);
    return wrap;
  }

  function cleanToolKind(kind) {
    const normalized = String(kind || 'tool').toLowerCase();
    return ['read', 'search', 'write', 'delete', 'test', 'command'].includes(normalized)
      ? normalized
      : 'tool';
  }

  function currentAgentAliases() {
    const aliases = {};
    for (const id of AGENT_IDS) {
      aliases[id] = (latestAgents[id] && latestAgents[id].alias) || id;
    }
    return aliases;
  }

  function mentionEntries() {
    const aliases = currentAgentAliases();
    const entries = [];
    for (const id of AGENT_IDS) {
      entries.push([id, id]);
      const alias = String(aliases[id] || '').trim().replace(/^@/, '').toLowerCase();
      if (alias && alias !== id) entries.push([alias, id]);
    }
    entries.sort((a, b) => b[0].length - a[0].length);
    return entries;
  }

  function mentionRegex() {
    const names = mentionEntries().map(([name]) => name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    return new RegExp('@(' + names.join('|') + ')(?!\\w)', 'gi');
  }

  function agentMentions(text) {
    const matches = [];
    const seen = new Set();
    const withoutCodeBlocks = String(text || '').replace(/```[\s\S]*?```/g, ' ');
    const withoutInlineCode = withoutCodeBlocks.replace(/`[^`\n]*`/g, ' ');
    const aliasToAgent = Object.fromEntries(mentionEntries());
    for (const match of withoutInlineCode.matchAll(mentionRegex())) {
      const agent = aliasToAgent[String(match[1]).toLowerCase()];
      if (agent && !seen.has(agent)) {
        seen.add(agent);
        matches.push(agent);
      }
    }
    return matches;
  }

  function buildTurnMetadata(turn, turnIdx, tokenInfo, totalTurns) {
    const session = activeSession();
    const status = latestStatus || {};
    const body = turn.body || '';
    return {
      copied_at: new Date().toISOString(),
      session: session ? {
        id: session.id,
        name: session.name,
        project_root: session.project_root,
      } : { id: activeSessionId },
      turn: {
        index: turnIdx,
        total_turns: totalTurns,
        author: turn.author,
        display_time: turn.time,
        body_chars: body.length,
        body_lines: body ? body.split('\n').length : 0,
        activation_mentions: agentMentions(body),
      },
      event_metadata: tokenInfo ? {
        dispatch_mode: tokenInfo.metadata && tokenInfo.metadata.dispatch_mode || null,
        prompt_tokens_est: tokenInfo.prompt_tokens_est || null,
        response_tokens_est: tokenInfo.response_tokens_est || null,
        token_usage: tokenInfo.token_usage || null,
        raw_metadata: tokenInfo.metadata || {},
      } : null,
      dispatch_state: {
        busy: Boolean(status.busy),
        current_agent: status.current_agent || status.agent || null,
        current_dispatch: status.current_dispatch || null,
        active_dispatches: status.active_dispatches || [],
        dispatch_queue: status.dispatch_queue || [],
      },
      recent_trace: latestTraceEvents.slice(-10),
      agents: status.agents || latestAgents || {},
      note: 'Metadata only; message body is summarized by length and mentions, not copied.',
    };
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
  }

  async function copyTurnMetadata(turn, turnIdx, tokenInfo, totalTurns) {
    const metadata = buildTurnMetadata(turn, turnIdx, tokenInfo, totalTurns);
    await copyText(JSON.stringify(metadata, null, 2));
  }

  function colorMentions(container) {
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null, false);
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);
    const mentionRE = mentionRegex();
    const aliasToAgent = Object.fromEntries(mentionEntries());
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
        span.className = `mention ${aliasToAgent[String(m[1]).toLowerCase()] || m[1].toLowerCase()}`;
        span.textContent = m[0];
        frag.appendChild(span);
        lastIdx = mentionRE.lastIndex;
      }
      if (lastIdx < text.length) frag.appendChild(document.createTextNode(text.slice(lastIdx)));
      node.parentNode.replaceChild(frag, node);
    }
  }

  const FILE_PATH_RE = /(?:^|[\s(])(@?(?:[a-zA-Z]:[\\/]|\/|\.\.?[\\/])?[\w.\-\\/]+[\\/][\w.\-\\/]*\.[a-zA-Z]{1,8}|@\.?[\w-]+(?:\.[\w-]+)+)(?=[\s,;:.)'"\]>]|$)/g;

  function linkFiles(container) {
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null, false);
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);
    for (const node of textNodes) {
      const parent = node.parentNode;
      if (!parent) continue;
      if (parent.closest('pre, code, a, .file-link, .mention, .turn-erase-btn, button, input, textarea, select')) continue;
      const text = node.textContent;
      if (!/\.[a-zA-Z0-9]/.test(text)) continue;
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
        link.title = 'File actions';
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

  function lastAgentSpeaker() {
    for (let i = latestTurns.length - 1; i >= 0; i--) {
      const author = latestTurns[i] && latestTurns[i].author;
      if (AGENT_IDS.includes(author)) return author;
    }
    return null;
  }

  function resolveQuickReply(text) {
    const trimmed = String(text || '').trim();
    const agent = lastAgentSpeaker();
    if (!agent) return trimmed;
    const info = latestAgents[agent] || {};
    const mention = '@' + (info.alias || agent);
    if (trimmed === '@@') return mention;
    if (trimmed.startsWith('@@ ')) return mention + trimmed.slice(2);
    return trimmed;
  }

  function escapeHTML(text) {
    return String(text || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function agentClassForMention(mention) {
    const raw = String(mention || '').replace(/^@/, '').toLowerCase();
    for (const id of AGENT_IDS) {
      const info = latestAgents[id] || {};
      if (raw === id || raw === String(info.alias || '').toLowerCase()) return id;
    }
    return '';
  }

  function composerTokenClass(token) {
    if (token.startsWith('```')) return 'md-code md-code-block';
    if (token.startsWith('`')) return 'md-code';
    if (token.startsWith('@')) {
      const agentClass = agentClassForMention(token);
      if (agentClass) return `mention ${agentClass}`;
      if (/\.[a-zA-Z0-9]/.test(token)) return 'file-link';
    }
    if (/\.[a-zA-Z0-9]/.test(token)) return 'file-link';
    return '';
  }

  function renderComposerHighlights(text) {
    const tokenRe = /```[\s\S]*?```|``[^`\n]*(?:`(?!`)[^`\n]*)*``|`[^`\n`]*`|@(?:claude|codex|deepseek|[A-Za-z][\w-]*|\.[\w-]+(?:\.[\w-]+)+)|(?:[a-zA-Z]:[\\/]|\/|\.\.?[\\/])?[\w.\-\\/]+[\\/][\w.\-\\/]*\.[a-zA-Z]{1,8}/g;
    let html = '';
    let lastIdx = 0;
    let m;
    while ((m = tokenRe.exec(text)) !== null) {
      const token = m[0];
      const cls = composerTokenClass(token);
      html += escapeHTML(text.slice(lastIdx, m.index));
      html += cls ? `<span class="${cls}">${escapeHTML(token)}</span>` : escapeHTML(token);
      lastIdx = m.index + token.length;
    }
    html += escapeHTML(text.slice(lastIdx));
    if (text.endsWith('\n')) html += '\n';
    return html || '&nbsp;';
  }

  function updateComposerHighlights() {
    const text = resolveQuickReply(msgInput.value);
    msgHighlights.innerHTML = renderComposerHighlights(text);
    msgInput.classList.toggle('has-highlight', Boolean(msgInput.value));
    msgHighlights.scrollTop = msgInput.scrollTop;
    msgHighlights.scrollLeft = msgInput.scrollLeft;
  }

  function insertAtCursor(text) {
    const value = msgInput.value;
    const start = msgInput.selectionStart || 0;
    const end = msgInput.selectionEnd || start;
    const prefix = start > 0 && value[start - 1] !== '\n' ? '\n' : '';
    const suffix = end < value.length && value[end] !== '\n' ? '\n' : '';
    const inserted = prefix + text + suffix;
    msgInput.value = value.slice(0, start) + inserted + value.slice(end);
    const caret = start + inserted.length;
    msgInput.setSelectionRange(caret, caret);
    msgInput.focus();
    updateComposerHighlights();
  }

  function readFileAsDataURL(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ''));
      reader.onerror = () => reject(reader.error || new Error('failed to read image'));
      reader.readAsDataURL(file);
    });
  }

  async function uploadPastedImage(file) {
    const data = await readFileAsDataURL(file);
    const res = await fetch(sessionApi('/attachments'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ media_type: file.type, data }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(payload.error || 'image upload failed');
    return payload;
  }

  async function handleImagePaste(e) {
    if (!activeSessionId || !e.clipboardData || !e.clipboardData.files) return;
    const images = Array.from(e.clipboardData.files)
      .filter((file) => file && file.type && file.type.startsWith('image/'));
    if (images.length === 0) return;
    e.preventDefault();
    for (const image of images) {
      try {
        const uploaded = await uploadPastedImage(image);
        insertAtCursor(uploaded.markdown || `![pasted image](${uploaded.url})`);
      } catch (err) {
        console.error('Paste image upload failed:', err);
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
    const resolvedFilePath = String(filePath || '').replace(/^@/, '');
    const menu = document.createElement('div');
    menu.className = 'file-menu';
    menu.innerHTML = `
      <button class="file-menu-item" data-action="open">Open file</button>
      <button class="file-menu-item" data-action="explorer">Open in explorer</button>
      <button class="file-menu-item" data-action="copy">Copy path</button>
    `;
    menu.querySelector('[data-action="open"]').addEventListener('click', async () => {
      hideFileMenu();
      try {
        await fetch(sessionApi('/open-file'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: resolvedFilePath }),
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
          body: JSON.stringify({ path: resolvedFilePath }),
        });
      } catch (e) {
        console.error('Open explorer failed:', e);
      }
    });
    menu.querySelector('[data-action="copy"]').addEventListener('click', async () => {
      hideFileMenu();
      try {
        await copyText(resolvedFilePath);
      } catch (e) {
        console.error('Copy path failed:', e);
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

      const activity = session.activity || {};
      const runningCount = Number(activity.running || 0);
      const queuedCount = Number(activity.queued || 0);
      const unreadFinishedCount = Number(activity.unread_finished || 0);
      const counts = document.createElement('span');
      counts.className = 'session-counts';
      if (runningCount) {
        const chip = document.createElement('span');
        chip.className = 'session-count running';
        chip.textContent = `run ${runningCount}`;
        chip.title = `${runningCount} running quer${runningCount === 1 ? 'y' : 'ies'}`;
        counts.appendChild(chip);
      }
      if (queuedCount) {
        const chip = document.createElement('span');
        chip.className = 'session-count queued';
        chip.textContent = `queue ${queuedCount}`;
        chip.title = `${queuedCount} queued quer${queuedCount === 1 ? 'y' : 'ies'}`;
        counts.appendChild(chip);
      }
      if (unreadFinishedCount) {
        const chip = document.createElement('span');
        chip.className = 'session-count unread';
        chip.textContent = `new ${unreadFinishedCount}`;
        chip.title = `${unreadFinishedCount} finished response${unreadFinishedCount === 1 ? '' : 's'} pending read`;
        counts.appendChild(chip);
      }

      const ctxWrap = document.createElement('span');
      ctxWrap.className = 'session-ctx-wrap';
      ctxWrap.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
      });

      const ctxBtn = document.createElement('span');
      ctxBtn.className = 'session-ctx-btn';
      ctxBtn.textContent = '...';
      ctxBtn.title = 'Session actions';
      ctxBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        toggleSessionCtx(session, ctxBtn);
      });

      const ctxMenu = document.createElement('div');
      ctxMenu.className = 'session-ctx-menu hidden';

      const openFolderItem = document.createElement('button');
      openFolderItem.type = 'button';
      openFolderItem.className = 'session-ctx-item';
      openFolderItem.textContent = 'Open folder';
      openFolderItem.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        openSessionFolder(session);
        closeSessionCtxMenus();
      });

      const copyPathItem = document.createElement('button');
      copyPathItem.type = 'button';
      copyPathItem.className = 'session-ctx-item';
      copyPathItem.textContent = 'Copy path';
      copyPathItem.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        copySessionPath(session);
        closeSessionCtxMenus();
      });

      ctxMenu.appendChild(openFolderItem);
      ctxMenu.appendChild(copyPathItem);
      ctxWrap.appendChild(ctxBtn);
      ctxWrap.appendChild(ctxMenu);

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
      if (counts.children.length) item.appendChild(counts);
      item.appendChild(ctxWrap);
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
    latestAgentsMd = null;
    userScrolledUp = false;
    renderSessions();
    connectWS();
    await Promise.all([fetchChat(), fetchStatus(), fetchTrace(), fetchAgentsMd()]);
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
    latestAgentsMd = null;
    renderSessions();
    connectWS();
    await Promise.all([fetchChat(), fetchStatus(), fetchTrace(), fetchAgentsMd()]);
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
    latestAgentsMd = null;
    renderSessions();
    connectWS();
    await Promise.all([fetchChat(), fetchStatus(), fetchTrace(), fetchAgentsMd()]);
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

  function sessionProjectPath(session) {
    return session.project_root || '';
  }

  function toggleSessionCtx(session, btn) {
    const menu = btn.parentElement.querySelector('.session-ctx-menu');
    if (!menu) return;
    const isOpen = !menu.classList.contains('hidden');
    closeSessionCtxMenus();
    if (!isOpen) {
      menu.classList.remove('hidden');
    }
  }

  function closeSessionCtxMenus() {
    document.querySelectorAll('.session-ctx-menu').forEach((m) => m.classList.add('hidden'));
  }

  function openSessionFolder(session) {
    fetch('/api/sessions/' + encodeURIComponent(session.id) + '/open-explorer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: '.' }),
    }).catch((e) => console.error('open folder failed:', e));
  }

  function copySessionPath(session) {
    const path = sessionProjectPath(session);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(path).catch((e) => console.error('copy failed:', e));
    } else {
      const ta = document.createElement('textarea');
      ta.value = path;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
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
      latestTurns = turns;
      let tokenData = null;
      if (eventsRes.ok) {
        const eventsData = await eventsRes.json();
        const td = {};
        const byKey = {};
        eventsData.turns.forEach((evt, i) => {
          const meta = evt.metadata || {};
          if (evt.prompt_tokens_est || evt.response_tokens_est || meta.dispatch_mode || (evt.tool_calls && evt.tool_calls.length)) {
            td[i] = evt;
            const key = turnEventKey(evt.author, evt.display_ts);
            if (!byKey[key]) byKey[key] = [];
            byKey[key].push(evt);
          }
        });
        if (Object.keys(byKey).length > 0) td.byKey = byKey;
        if (Object.keys(td).length > 0) tokenData = td;
      }
      renderTurns(turns, tokenData);
      loadSessions().catch((e) => console.error('Failed to refresh sessions:', e));
      fetchStatus().catch((e) => console.error('Failed to refresh status:', e));
    } catch (e) {
      console.error('Failed to fetch chat:', e);
    }
  }

  async function sendMessage(dispatchMode = 'parallel') {
    if (!activeSessionId) return;
    const text = resolveQuickReply(msgInput.value.trim());
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
    updateComposerHighlights();
    try {
      const res = await fetch(sessionApi('/send'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, dispatch_mode: dispatchMode }),
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
      'This will summarize older turns into an authoritative shared summary, then keep any new turns written while compaction is running. Council will refuse if agents are still thinking or queued.',
      async () => {
        try {
          const res = await fetch(sessionApi('/compact'), { method: 'POST' });
          if (!res.ok) {
            const err = await res.json();
            window.alert(err.error || res.statusText || 'Compact failed');
            console.error('Compact failed:', err.error || err);
          }
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

  function turnIdentity(turn, turnIdx) {
    return {
      index: turnIdx,
      author: turn.author,
      display_ts: turn.time,
      original_text: turn.body || '',
    };
  }

  async function startEditTurn(turn, turnIdx) {
    if (isRenderingChat) return;
    editState = turnIdentity(turn, turnIdx);
    editError.textContent = '';
    editTextarea.value = turn.body || '';
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
    const newText = editTextarea.value;
    editSaveBtn.disabled = true;
    editError.textContent = '';
    try {
      const res = await fetch(sessionApi('/edit_turn'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...editState, text: newText }),
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

  async function eraseTurn(turn, turnIdx) {
    if (!activeSessionId) return;
    showConfirm(
      'Erase message?',
      'Delete this message permanently?',
      async () => {
        try {
          const res = await fetch(sessionApi('/erase_turn'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(turnIdentity(turn, turnIdx)),
          });
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            window.alert(err.error || res.statusText || 'Erase failed');
            return;
          }
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
      latestStatus = data;
      updateStatus(data);
    } catch (_) {}
  }

  async function fetchAgentsMd() {
    if (!activeSessionId) return;
    const targetSessionId = activeSessionId;
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(targetSessionId)}/agents-md`);
      const data = await res.json();
      if (targetSessionId !== activeSessionId) return;
      latestAgentsMd = data;
      if (!configPanel.classList.contains('hidden')) {
        renderConfig(
          globalConfigCheckbox.checked ? latestGlobalAgents : latestAgents,
          globalConfigCheckbox.checked ? latestGlobalDispatch : latestDispatch
        );
      }
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

  async function saveAgentsMd(text) {
    if (!activeSessionId) return;
    configStatus.textContent = 'saving AGENTS.md...';
    try {
      const res = await fetch(sessionApi('/agents-md'), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        configStatus.textContent = data.error || 'AGENTS.md save failed';
        return;
      }
      latestAgentsMd = data;
      renderConfig(
        globalConfigCheckbox.checked ? latestGlobalAgents : latestAgents,
        globalConfigCheckbox.checked ? latestGlobalDispatch : latestDispatch
      );
      configStatus.textContent = 'AGENTS.md saved';
      setTimeout(() => {
        if (configStatus.textContent === 'AGENTS.md saved') configStatus.textContent = '';
      }, 1200);
    } catch (_) {
      configStatus.textContent = 'AGENTS.md save failed';
    }
  }

  async function fetchTrace() {
    if (!activeSessionId) return;
    const targetSessionId = activeSessionId;
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(targetSessionId)}/trace`);
      const data = await res.json();
      if (targetSessionId !== activeSessionId) return;
      latestTraceEvents = data.events || [];
      renderTrace(latestTraceEvents);
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
          latestStatus = msg;
          updateStatus(msg);
          loadSessions().catch((e) => console.error('Failed to refresh sessions:', e));
        } else if (msg.type === 'trace_update') {
          const traceEvent = msg.event || {};
          latestTraceEvents.push(traceEvent);
          latestTraceEvents = latestTraceEvents.slice(-100);
          appendTraceEvent(traceEvent);
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
    latestDispatch = data.dispatch || {};
    latestGlobalDispatch = data.global_dispatch || latestGlobalDispatch || {};
    renderAgentModels(latestAgents);
    updateAgentButtons();
    renderConfig(globalConfigCheckbox.checked ? latestGlobalAgents : latestAgents, globalConfigCheckbox.checked ? latestGlobalDispatch : latestDispatch);
    updateComposerHighlights();
    renderQueueStatus(data);
    renderCompactStatus(data.compact);

    if (data.busy) {
      statusIndicator.className = 'busy';
      const agentName = data.agent || '?';
      if (agentName === 'summarizer') {
        statusIndicator.innerHTML = '&#9679; compacting&hellip;';
      } else if (agentName === 'multiple') {
        statusIndicator.innerHTML = '&#9679; agents thinking&hellip;';
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

  function renderCompactStatus(compact) {
    if (!compactStatusEl) return;
    if (!compact || !compact.enabled) {
      compactStatusEl.className = 'disabled';
      compactStatusEl.textContent = 'compact off';
      compactStatusEl.title = 'Auto compact is disabled';
      return;
    }

    const remaining = Number(compact.remaining_lines || 0);
    const remainingPct = Number(compact.remaining_percent || 0);
    compactStatusEl.className = compact.over_threshold
      ? 'danger'
      : (compact.warning ? 'warning' : '');
    compactStatusEl.textContent = compact.over_threshold
      ? 'compact due'
      : `compact ${remainingPct}% left`;
    compactStatusEl.title = `${remaining} lines until auto compact (${compact.line_count}/${compact.threshold_lines})`;
  }

  function dispatchLabel(item) {
    if (!item || !item.agent) return '@?';
    const source = item.source === 'user' ? 'from you' : 'from agent';
    return '@' + item.agent + ' ' + source;
  }

  function renderQueueStatus(data) {
    const active = data.active_dispatches || [];
    const current = data.current_dispatch || active[0] || null;
    const queue = data.dispatch_queue || [];
    const running = active.length || (current ? 1 : 0);
    const total = running + queue.length;
    if (!total) {
      queueBtn.classList.add('hidden');
      queueMenu.classList.add('hidden');
      queueMenu.innerHTML = '';
      return;
    }
    queueBtn.classList.remove('hidden');
    queueBtn.textContent = queue.length ? `Queue ${total}` : 'Queue 1';
    queueMenu.innerHTML = '';

    if (active.length) {
      active.forEach((item, idx) => {
        queueMenu.appendChild(makeQueueItem(item, active.length > 1 ? `Running ${idx + 1}` : 'Running'));
      });
    } else if (current) {
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
    const order = AGENT_IDS;
    agentModelsEl.innerHTML = '';
    for (const id of order) {
      const info = agents[id];
      if (!info) continue;
      const pill = document.createElement('span');
      pill.className = `agent-model ${id}`;
      pill.title = `${info.runtime || ''} - ${info.note || ''}`.trim();
      const effort = info.effort ? ` / ${info.effort}` : '';
      const provider = info.provider ? `${info.provider}: ` : '';
      const key = info.api_key_saved ? ' / key' : '';
      pill.textContent = `@${info.alias || id}: ${provider}${info.model || info.runtime || 'default'}${effort}${key}`;
      agentModelsEl.appendChild(pill);
    }
  }

  function updateAgentButtons() {
    agentButtons.forEach((btn) => {
      const id = btn.dataset.agent;
      const info = latestAgents[id] || {};
      btn.textContent = '@' + (info.alias || id);
    });
    const aliases = AGENT_IDS.map((id) => '@' + ((latestAgents[id] && latestAgents[id].alias) || id));
    msgInput.placeholder = aliases.join(' / ') + ' activates that agent. Use plain names when not summoning.';
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

  const PROVIDER_OPTIONS = [
    ['claude_cli', 'Claude CLI'],
    ['codex_cli', 'Codex CLI'],
    ['opencode', 'OpenCode'],
    ['hermes_api', 'Hermes API'],
    ['openrouter', 'OpenRouter'],
    ['deepseek_api', 'Deepseek API'],
    ['custom', 'Custom'],
  ];

  function makeProviderSelect(agentId, value) {
    const select = document.createElement('select');
    select.dataset.agent = agentId;
    select.dataset.section = 'providers';
    for (const [id, label] of PROVIDER_OPTIONS) {
      const option = document.createElement('option');
      option.value = id;
      option.textContent = label;
      select.appendChild(option);
    }
    select.value = value || (agentId === 'claude' ? 'claude_cli' : agentId === 'codex' ? 'codex_cli' : agentId === 'hermes' ? 'hermes_api' : 'opencode');
    select.addEventListener('change', () => {
      patchConfig({ providers: { [agentId]: select.value } });
    });
    return select;
  }

  function defaultAttachmentPolicy(agentId) {
    return agentId === 'deepseek' ? 'placeholder' : 'path-visible';
  }

  function makeAttachmentPolicySelect(agentId, value) {
    const select = document.createElement('select');
    select.dataset.agent = agentId;
    select.dataset.section = 'dispatch.attachments';
    for (const [id, label] of ATTACHMENT_POLICY_OPTIONS) {
      const option = document.createElement('option');
      option.value = id;
      option.textContent = label;
      select.appendChild(option);
    }
    select.value = value || defaultAttachmentPolicy(agentId);
    select.addEventListener('change', () => {
      patchConfig({ dispatch: { attachments: { [agentId]: select.value } } });
    });
    return select;
  }

  function makeModelInput(agentId, value, options) {
    const input = document.createElement('input');
    input.type = 'text';
    input.value = value || '';
    input.placeholder = agentId === 'deepseek' ? 'deepseek/deepseek-v4-pro' : agentId === 'hermes' ? 'hermes-agent' : 'model id';
    input.setAttribute('list', `model-options-${agentId}`);
    input.addEventListener('change', () => {
      patchConfig({ models: { [agentId]: input.value.trim() } });
    });
    const list = document.createElement('datalist');
    list.id = `model-options-${agentId}`;
    for (const opt of optionList(options, value)) {
      const option = document.createElement('option');
      option.value = opt;
      list.appendChild(option);
    }
    const frag = document.createDocumentFragment();
    frag.appendChild(input);
    frag.appendChild(list);
    return frag;
  }

  function makeSecretInput(keyName, saved, disabled) {
    const input = document.createElement('input');
    input.type = 'password';
    input.autocomplete = 'off';
    input.disabled = Boolean(disabled);
    input.placeholder = disabled ? 'managed by selected CLI' : (saved ? 'saved - enter new key to replace' : 'paste API key');
    input.addEventListener('change', () => {
      if (input.disabled) return;
      const value = input.value.trim();
      if (!value) return;
      patchConfig({ api_keys: { [keyName]: value } });
      input.value = '';
      input.placeholder = 'saved - enter new key to replace';
    });
    return input;
  }

  function apiKeyNameFor(agentId, provider) {
    if (provider === 'openrouter') return 'openrouter';
    if (provider === 'deepseek_api') return 'deepseek';
    return agentId;
  }

  function renderConfig(agents, dispatchCfg) {
    if (configPanel.classList.contains('hidden')) return;
    const order = AGENT_IDS;
    configGrid.innerHTML = '';
    for (const id of order) {
      const info = agents[id];
      if (!info) continue;

      const row = document.createElement('div');
      row.className = `config-row ${id}`;

      const label = document.createElement('div');
      label.className = 'config-agent';
      label.textContent = `@${info.alias || id}`;

      const aliasWrap = document.createElement('label');
      aliasWrap.textContent = 'Mention';
      const alias = document.createElement('input');
      alias.type = 'text';
      alias.value = info.alias || id;
      alias.placeholder = id;
      alias.addEventListener('change', () => {
        patchConfig({ aliases: { [id]: alias.value.trim().replace(/^@/, '') || id } });
      });
      aliasWrap.appendChild(alias);

      const providerWrap = document.createElement('label');
      providerWrap.textContent = 'Provider';
      providerWrap.appendChild(makeProviderSelect(id, info.provider || ''));

      const modelWrap = document.createElement('label');
      modelWrap.textContent = 'Model';
      modelWrap.appendChild(makeModelInput(id, info.model || '', info.model_options || []));

      const effortWrap = document.createElement('label');
      effortWrap.textContent = 'Effort';
      effortWrap.appendChild(makeSelect(id, 'effort', info.effort || '', info.effort_options || []));

      const keyWrap = document.createElement('label');
      const cliManaged = ['claude_cli', 'codex_cli', 'opencode'].includes(info.provider || '');
      keyWrap.textContent = info.provider === 'openrouter'
        ? 'OpenRouter key'
        : info.provider === 'deepseek_api'
          ? 'Deepseek key'
          : 'API key';
      keyWrap.appendChild(makeSecretInput(apiKeyNameFor(id, info.provider || ''), info.api_key_saved, cliManaged));

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
      row.appendChild(aliasWrap);
      row.appendChild(providerWrap);
      row.appendChild(modelWrap);
      row.appendChild(effortWrap);
      row.appendChild(keyWrap);
      row.appendChild(roleWrap);
      configGrid.appendChild(row);
    }

    const flashInfo = agents.deepseek || {};
    const flashSection = document.createElement('div');
    flashSection.className = 'config-section config-flash-section';
    const flashLabel = document.createElement('div');
    flashLabel.className = 'config-section-label';
    flashLabel.textContent = 'Deepseek Flash summarizer';
    flashSection.appendChild(flashLabel);
    const flashModelWrap = document.createElement('label');
    flashModelWrap.textContent = 'Flash model';
    const flashModel = document.createElement('input');
    flashModel.type = 'text';
    flashModel.value = flashInfo.flash_model || 'deepseek/deepseek-v4-flash';
    flashModel.addEventListener('change', () => {
      patchConfig({ models: { deepseek_flash: flashModel.value.trim() } });
    });
    flashModelWrap.appendChild(flashModel);
    const flashKeyWrap = document.createElement('label');
    flashKeyWrap.textContent = 'Flash token';
    flashKeyWrap.appendChild(makeSecretInput('deepseek_flash', flashInfo.flash_key_saved));
    flashSection.appendChild(flashModelWrap);
    flashSection.appendChild(flashKeyWrap);
    configGrid.appendChild(flashSection);

    const dispatchSection = document.createElement('div');
    dispatchSection.className = 'config-section';
    const dispatchLabel = document.createElement('div');
    dispatchLabel.className = 'config-section-label';
    dispatchLabel.textContent = 'Dispatch limits';
    dispatchSection.appendChild(dispatchLabel);

    const dispatchKeys = ['chain_depth_limit', 'timeout_seconds', 'max_prompt_chars'];
    for (const key of dispatchKeys) {
      const wrap = document.createElement('label');
      wrap.textContent = key;
      const input = document.createElement('input');
      input.type = 'number';
      input.min = '0';
      input.value = dispatchCfg && dispatchCfg[key] !== undefined ? dispatchCfg[key] : '';
      input.addEventListener('change', () => {
        patchConfig({ dispatch: { [key]: input.value } });
      });
      wrap.appendChild(input);
      dispatchSection.appendChild(wrap);
    }
    configGrid.appendChild(dispatchSection);

    const hermesDispatch = dispatchCfg && dispatchCfg.hermes || {};
    const hermesSection = document.createElement('div');
    hermesSection.className = 'config-section';
    const hermesLabel = document.createElement('div');
    hermesLabel.className = 'config-section-label';
    hermesLabel.textContent = 'Hermes/API bridge';
    hermesSection.appendChild(hermesLabel);

    const hermesUrlWrap = document.createElement('label');
    hermesUrlWrap.textContent = 'Base URL';
    const hermesUrl = document.createElement('input');
    hermesUrl.type = 'text';
    hermesUrl.value = hermesDispatch.base_url || 'http://127.0.0.1:8642/v1';
    hermesUrl.addEventListener('change', () => {
      patchConfig({ dispatch: { hermes: { base_url: hermesUrl.value.trim() } } });
    });
    hermesUrlWrap.appendChild(hermesUrl);
    hermesSection.appendChild(hermesUrlWrap);

    const hermesSessionWrap = document.createElement('label');
    hermesSessionWrap.textContent = 'Session key';
    const hermesSessionKey = document.createElement('input');
    hermesSessionKey.type = 'text';
    hermesSessionKey.value = hermesDispatch.session_key || '';
    hermesSessionKey.placeholder = 'auto from Council project';
    hermesSessionKey.addEventListener('change', () => {
      patchConfig({ dispatch: { hermes: { session_key: hermesSessionKey.value.trim() } } });
    });
    hermesSessionWrap.appendChild(hermesSessionKey);
    hermesSection.appendChild(hermesSessionWrap);

    const hermesHeaderWrap = document.createElement('label');
    hermesHeaderWrap.textContent = 'Session header';
    const hermesSessionHeader = document.createElement('input');
    hermesSessionHeader.type = 'text';
    hermesSessionHeader.value = hermesDispatch.session_header !== undefined
      ? hermesDispatch.session_header
      : 'X-Hermes-Session-Key';
    hermesSessionHeader.placeholder = 'blank disables session header';
    hermesSessionHeader.addEventListener('change', () => {
      patchConfig({ dispatch: { hermes: { session_header: hermesSessionHeader.value.trim() } } });
    });
    hermesHeaderWrap.appendChild(hermesSessionHeader);
    hermesSection.appendChild(hermesHeaderWrap);
    configGrid.appendChild(hermesSection);

    const attachmentSection = document.createElement('div');
    attachmentSection.className = 'config-section config-attachment-section';
    const attachmentLabel = document.createElement('div');
    attachmentLabel.className = 'config-section-label';
    attachmentLabel.textContent = 'Attachment prompts';
    attachmentSection.appendChild(attachmentLabel);

    const attachmentPolicies = dispatchCfg && dispatchCfg.attachments || {};
    for (const id of order) {
      const info = agents[id];
      if (!info) continue;
      const wrap = document.createElement('label');
      wrap.textContent = `@${info.alias || id}`;
      wrap.appendChild(makeAttachmentPolicySelect(id, attachmentPolicies[id]));
      attachmentSection.appendChild(wrap);
    }
    configGrid.appendChild(attachmentSection);

    const agentsDocSection = document.createElement('div');
    agentsDocSection.className = 'config-section config-agents-md-section';
    const agentsDocLabel = document.createElement('div');
    agentsDocLabel.className = 'config-section-label';
    agentsDocLabel.textContent = 'Session AGENTS.md';
    agentsDocSection.appendChild(agentsDocLabel);

    const agentsDocMeta = document.createElement('div');
    agentsDocMeta.className = 'config-agents-md-meta';
    if (globalConfigCheckbox.checked) {
      agentsDocMeta.textContent = 'session only';
    } else if (latestAgentsMd && latestAgentsMd.fallback) {
      agentsDocMeta.textContent = `seeded from ${latestAgentsMd.fallback_filename || 'CLAUDE.md'}`;
    } else if (latestAgentsMd && latestAgentsMd.exists) {
      agentsDocMeta.textContent = latestAgentsMd.filename || 'AGENTS.md';
    } else {
      agentsDocMeta.textContent = 'new AGENTS.md';
    }
    agentsDocSection.appendChild(agentsDocMeta);

    const agentsDoc = document.createElement('textarea');
    agentsDoc.rows = 9;
    agentsDoc.spellcheck = false;
    agentsDoc.disabled = globalConfigCheckbox.checked;
    agentsDoc.placeholder = globalConfigCheckbox.checked
      ? 'Switch off global defaults to edit this session project.'
      : '# Project instructions for all Council agents';
    agentsDoc.value = globalConfigCheckbox.checked ? '' : ((latestAgentsMd && latestAgentsMd.text) || '');
    agentsDoc.addEventListener('change', () => {
      if (!agentsDoc.disabled) saveAgentsMd(agentsDoc.value);
    });
    agentsDocSection.appendChild(agentsDoc);
    configGrid.appendChild(agentsDocSection);
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
      kind.textContent = it.kind === 'command' ? 'cmd' : (it.kind === 'agent' ? 'agent' : (it.kind === 'quick' ? '@@' : 'file'));
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

  function quickReplyItem() {
    const agent = lastAgentSpeaker();
    if (!agent) return null;
    const info = latestAgents[agent] || {};
    const mention = '@' + (info.alias || agent);
    return {
      label: '@@ -> ' + mention + ' - reply to last agent',
      insert: mention,
      kind: 'quick',
    };
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
    } else if (acState.triggerChar === '@@') {
      const item = quickReplyItem();
      acState.items = item ? [item] : [];
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
    const triggerLen = acState.triggerChar === '@@' ? 2 : 1;
    const afterStart = acState.triggerStart + triggerLen + acState.query.length;
    const after = val.slice(afterStart);
    const inserted = acState.triggerChar === '@@' ? it.insert + ' ' : acState.triggerChar + it.insert + ' ';
    msgInput.value = before + inserted + after;
    const caret = (before + inserted).length;
    msgInput.setSelectionRange(caret, caret);
    closeAC();
    msgInput.focus();
    updateComposerHighlights();
  }

  function updateACFromInput() {
    const val = msgInput.value;
    const caret = msgInput.selectionStart;
    const quickStart = caret - 2;
    if (
      quickStart >= 0 &&
      val.slice(quickStart, caret) === '@@' &&
      (quickStart === 0 || /\s/.test(val[quickStart - 1]))
    ) {
      acState.open = true;
      acState.triggerStart = quickStart;
      acState.triggerChar = '@@';
      acState.query = '';
      refreshAC();
      return;
    }
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

  msgInput.addEventListener('input', () => {
    updateACFromInput();
    updateComposerHighlights();
  });
  msgInput.addEventListener('click', () => {
    updateACFromInput();
    updateComposerHighlights();
  });
  msgInput.addEventListener('keyup', (e) => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) {
      updateACFromInput();
    }
    updateComposerHighlights();
  });
  msgInput.addEventListener('scroll', () => {
    msgHighlights.scrollTop = msgInput.scrollTop;
    msgHighlights.scrollLeft = msgInput.scrollLeft;
  });
  msgInput.addEventListener('paste', handleImagePaste);
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
      sendMessage('queued');
    }
  });

  sendNowBtn.addEventListener('click', () => sendMessage('parallel'));
  sendBtn.addEventListener('click', () => sendMessage('queued'));
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
    if (openTurnMenu && !e.target.closest('.turn-more-wrap')) {
      openTurnMenu.classList.add('hidden');
      openTurnMenu = null;
    }
    if (!e.target.closest('.session-ctx-wrap')) {
      closeSessionCtxMenus();
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
    if (e.key === 'Escape' && openTurnMenu) {
      openTurnMenu.classList.add('hidden');
      openTurnMenu = null;
    }
  });
  configToggleBtn.addEventListener('click', async () => {
    configPanel.classList.toggle('hidden');
    if (configPanel.classList.contains('hidden')) return;
    if (!latestAgentsMd) {
      configStatus.textContent = 'loading AGENTS.md...';
      await fetchAgentsMd();
      if (configStatus.textContent === 'loading AGENTS.md...') configStatus.textContent = '';
      return;
    }
    renderConfig(globalConfigCheckbox.checked ? latestGlobalAgents : latestAgents, globalConfigCheckbox.checked ? latestGlobalDispatch : latestDispatch);
  });
  globalConfigCheckbox.addEventListener('change', () => {
    renderConfig(globalConfigCheckbox.checked ? latestGlobalAgents : latestAgents, globalConfigCheckbox.checked ? latestGlobalDispatch : latestDispatch);
  });
  agentButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      const info = latestAgents[btn.dataset.agent] || {};
      const mention = '@' + (info.alias || btn.dataset.agent);
      const text = msgInput.value.trim();
      msgInput.value = text ? `${mention} ${text}` : `${mention} `;
      msgInput.focus();
      msgInput.setSelectionRange(msgInput.value.length, msgInput.value.length);
      updateComposerHighlights();
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
    await Promise.all([fetchChat(), fetchStatus(), fetchTrace(), fetchAgentsMd()]);
    setInterval(() => {
      loadSessions().catch(() => {});
    }, 3000);
  }

  init();
})();
