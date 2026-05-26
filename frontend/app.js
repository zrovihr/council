// Council App - Multi-session frontend logic

(() => {
  const chatInner = document.getElementById('chat-inner');
  const chatArea = document.getElementById('chat-area');
  const traceList = document.getElementById('trace-list');
  const globalLogList = document.getElementById('global-log-list');
  const msgInput = document.getElementById('msg-input');
  const msgHighlights = document.getElementById('msg-highlights');
  const sendNowBtn = document.getElementById('send-now-btn');
  const sendBtn = document.getElementById('send-btn');
  function getAgentPromptRows() { return document.querySelectorAll('.agent-prompt-name'); }
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
  const resetTokenStatsBtn = document.getElementById('reset-token-stats-btn');
  const queueBtn = document.getElementById('queue-btn');
  const queueMenu = document.getElementById('queue-menu');
  const projectNameEl = document.getElementById('project-name');
  const agentModelsEl = document.getElementById('agent-models');
  const agentPromptBarEl = document.getElementById('agent-prompt-bar');
  const mobileMenuBtn = document.getElementById('mobile-menu-btn');
  const mobileDrawerBackdrop = document.getElementById('mobile-drawer-backdrop');
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
  let agentsDocPanelOpen = false;
  let latestTraceEvents = [];
  let latestGlobalLogEvents = [];
  let latestTurns = [];
  let latestTokenData = null;
  let chatFetchFrame = null;
  let chatFetchQueued = false;
  let sessions = [];
  let activeSessionId = null;
  const sessionScrollStates = new Map();
  const SCROLL_STORAGE_KEY = 'council.sessionScrollStates.v1';
  let editState = null;
  let openTurnMenu = null;
  let renamingSessionId = null;
  let composerStickToBottom = false;
  let previewTimer = null;
  let lastPreviewDraft = '';
  let lastPreviewAgents = null;
  let lastSentPreviewAgents = null;
  let lastPreviewData = null;
  const DEFAULT_AGENT_IDS = ['claude', 'codex', 'deepseek', 'hermes'];
  let AGENT_IDS = [...DEFAULT_AGENT_IDS];
  const DEFAULT_COLORS = {
    you: '#ffffff',
    claude: '#7dd3fc',
    codex: '#c4b5fd',
    deepseek: '#fdba74',
    hermes: '#86efac',
    primary: '#00ff41',
  };
  const AGENT_COLORS = [
    '#7dd3fc', '#c4b5fd', '#fdba74', '#86efac',
    '#fca5a5', '#a5b4fc', '#fde047', '#5eead4',
    '#d8b4fe', '#f0abfc', '#67e8f9', '#fb923c',
  ];
  function getColorIds() { return ['you', ...AGENT_IDS]; }
  function colorForAny(id, colors) {
    if (colors && colors[id]) return colors[id];
    if (DEFAULT_COLORS[id]) return DEFAULT_COLORS[id];
    const idx = AGENT_IDS.indexOf(id);
    return AGENT_COLORS[idx >= 0 ? idx % AGENT_COLORS.length : 0];
  }
  const ATTACHMENT_POLICY_OPTIONS = [
    ['path-visible', 'Path visible'],
    ['placeholder', 'Placeholder only'],
  ];

  function isCompactingStatus(status = latestStatus) {
    return Boolean(status && (
      status.compacting || (status.busy && (status.current_agent || status.agent) === 'summarizer')
    ));
  }

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

  function loadStoredScrollStates() {
    try {
      const raw = localStorage.getItem(SCROLL_STORAGE_KEY);
      const data = raw ? JSON.parse(raw) : {};
      Object.entries(data || {}).forEach(([key, value]) => {
        if (value && typeof value === 'object') sessionScrollStates.set(key, value);
      });
    } catch (_) {}
  }

  function persistScrollStates() {
    try {
      localStorage.setItem(
        SCROLL_STORAGE_KEY,
        JSON.stringify(Object.fromEntries(sessionScrollStates.entries()))
      );
    } catch (_) {}
  }

  chatArea.addEventListener('scroll', () => {
    if (isRenderingChat) return;
    const atBottom = chatArea.scrollHeight - chatArea.scrollTop - chatArea.clientHeight < 60;
    userScrolledUp = !atBottom;
    saveActiveScrollState();
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

  function turnScrollKey(turn, turnIdx) {
    if (Number.isInteger(turn.event_line_idx)) return `event:${turn.event_line_idx}`;
    return `turn:${turnIdx}:${turn.author || ''}:${turn.time || ''}`;
  }

  function readChatScrollState() {
    const distanceFromBottom = chatArea.scrollHeight - chatArea.scrollTop - chatArea.clientHeight;
    const state = {
      scrollTop: chatArea.scrollTop,
      scrollHeight: chatArea.scrollHeight,
      distanceFromBottom,
      atBottom: distanceFromBottom < 60,
      anchorKey: null,
      anchorOffset: 0,
    };

    const cards = chatInner.querySelectorAll('.turn-card[data-scroll-key]');
    for (const card of cards) {
      if (card.offsetTop + card.offsetHeight >= chatArea.scrollTop) {
        state.anchorKey = card.dataset.scrollKey;
        state.anchorOffset = card.offsetTop - chatArea.scrollTop;
        break;
      }
    }
    return state;
  }

  function saveActiveScrollState(force = false) {
    if (!activeSessionId || (isRenderingChat && !force)) return;
    sessionScrollStates.set(activeSessionId, readChatScrollState());
    persistScrollStates();
  }

  function escapeCssValue(value) {
    if (window.CSS && typeof window.CSS.escape === 'function') {
      return window.CSS.escape(value);
    }
    return String(value).replace(/["\\]/g, '\\$&');
  }

  function restoreChatScrollState(state) {
    if (!state || state.atBottom) {
      userScrolledUp = false;
      chatArea.scrollTop = chatArea.scrollHeight;
      newMsgsBtn.classList.add('hidden');
      return;
    }

    userScrolledUp = true;
    newMsgsBtn.classList.remove('hidden');
    const maxScroll = Math.max(0, chatArea.scrollHeight - chatArea.clientHeight);

    if (state.anchorKey) {
      const selector = `.turn-card[data-scroll-key="${escapeCssValue(state.anchorKey)}"]`;
      const anchor = chatInner.querySelector(selector);
      if (anchor) {
        chatArea.scrollTop = Math.max(0, anchor.offsetTop - state.anchorOffset);
      } else {
        chatArea.scrollTop = maxScroll;
      }
      return;
    }

    const desiredTop = state.scrollTop || 0;
    chatArea.scrollTop = desiredTop <= maxScroll ? desiredTop : maxScroll;
  }

  function scrollStateForRender(sessionId) {
    if (sessionId && sessionId === activeSessionId && chatInner.children.length) {
      return readChatScrollState();
    }
    return sessionScrollStates.get(sessionId);
  }

  function markActiveScrollAtBottom() {
    if (!activeSessionId) return;
    userScrolledUp = false;
    newMsgsBtn.classList.add('hidden');
    sessionScrollStates.set(activeSessionId, {
      scrollTop: chatArea.scrollHeight,
      scrollHeight: chatArea.scrollHeight,
      distanceFromBottom: 0,
      atBottom: true,
      anchorKey: null,
      anchorOffset: 0,
    });
    persistScrollStates();
  }

  function parseChatMD(text) {
    const turns = [];
    const TURN_HEADER_RE = /^##\s+\[@([\w-]+)\]\s+(.+)$/;
    const ESCAPED_TURN_HEADER_RE = /^\\(##\s+\[@[\w-]+\]\s+.+)$/;
    const lines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
    let current = null;
    let preamble = '';
    let fenceMarker = '';
    let fenceLen = 0;
    let inCompactionBody = false;
    let compactionPendingMarkerSeen = false;
    let skippingDuplicatedChat = false;
    let compactionCutoff = '';

    function compactStamp(value) {
      const m = /^compacted\s+(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})/i.exec(value || '');
      return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}` : '';
    }

    function turnStamp(value) {
      const m = /^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})/.exec(value || '');
      return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}` : '';
    }

    function isPendingMarker(line) {
      return /^(?:No pending mentions\.|Pending mention:\s+@\w+)\s*$/.test(line);
    }

    function isSingleLineFence(line, markerText) {
      return line.slice(line.indexOf(markerText) + markerText.length).includes(markerText);
    }

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

    function appendBody(line) {
      const escapedHeader = ESCAPED_TURN_HEADER_RE.exec(line);
      const bodyLine = escapedHeader ? escapedHeader[1] : line;
      if (current) {
        if (current.body) current.body += '\n';
        current.body += bodyLine;
      } else {
        if (preamble) preamble += '\n';
        preamble += bodyLine;
      }
    }

    for (const line of lines) {
      const fence = /^\s*(```+|~~~+)/.exec(line);
      const m = fenceMarker ? null : TURN_HEADER_RE.exec(line);
      if (m) {
        if (skippingDuplicatedChat) {
          const ts = turnStamp(m[2]);
          if (ts && compactionCutoff && ts <= compactionCutoff) {
            continue;
          }
          skippingDuplicatedChat = false;
          inCompactionBody = false;
          compactionPendingMarkerSeen = false;
        }
        if (inCompactionBody) {
          if (compactionPendingMarkerSeen) {
            if (m[1] === 'system' && m[2].startsWith('compacted ')) {
              skippingDuplicatedChat = true;
              continue;
            }
            inCompactionBody = false;
            compactionPendingMarkerSeen = false;
          } else {
            if (m[1] === 'system' && m[2].startsWith('compacted ')) {
              appendBody(line);
              continue;
            }
            inCompactionBody = false;
          }
        }
        if (!current && turns.length === 0 && preamble.trim()) {
          turns.push(preambleTurn(preamble));
          preamble = '';
        }
        finishCurrent();
        current = { author: m[1], time: m[2], body: '' };
        if (m[1] === 'system' && m[2].startsWith('compacted ')) {
          inCompactionBody = true;
          compactionPendingMarkerSeen = false;
          skippingDuplicatedChat = false;
          compactionCutoff = compactStamp(m[2]);
        }
      } else {
        appendBody(line);
        if (inCompactionBody && !fenceMarker && isPendingMarker(line)) {
          compactionPendingMarkerSeen = true;
        }
        if (fence && !isSingleLineFence(line, fence[1])) {
          const markerText = fence[1];
          const marker = markerText[0];
          if (fenceMarker === marker && markerText.length >= fenceLen) {
            fenceMarker = '';
            fenceLen = 0;
          } else if (!fenceMarker) {
            fenceMarker = marker;
            fenceLen = markerText.length;
          }
        }
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

  function turnHeader(turn) {
    return `## [@${turn.author || 'system'}] ${turn.time || ''}`;
  }

  function matchingDispatchesForTurn(turn) {
    const status = latestStatus || {};
    const header = turnHeader(turn);
    const active = status.active_dispatches || [];
    const current = status.current_dispatch ? [status.current_dispatch] : [];
    const queued = status.dispatch_queue || [];
    return {
      active: active.concat(current).filter((item) => item && item.header === header),
      queued: queued.filter((item) => item && item.header === header),
    };
  }

  function isCompactionTurn(turn) {
    return turn.author === 'system' && /^compacted\b/i.test(String(turn.time || ''));
  }

  function filterTurnsByEvents(turns, eventTurns) {
    if (!Array.isArray(eventTurns) || eventTurns.length === 0) return turns;
    const byKey = {};
    eventTurns.forEach((evt) => {
      const key = turnEventKey(evt.author, evt.display_ts);
      if (!byKey[key]) byKey[key] = 0;
      byKey[key] += 1;
    });
    const used = {};
    return turns.filter((turn) => {
      if (isCompactionTurn(turn)) return true;
      const key = turnEventKey(turn.author, turn.time);
      const usedCount = used[key] || 0;
      if (!byKey[key] || usedCount >= byKey[key]) return false;
      used[key] = usedCount + 1;
      return true;
    });
  }

  function agentInfoForAuthor(author) {
    return latestAgents[author] || {};
  }

  function displayAuthor(author) {
    const info = agentInfoForAuthor(author);
    return info.alias || author;
  }

  function avatarClassForAuthor(author) {
    const info = agentInfoForAuthor(author);
    return info.runtime_family || author;
  }

  function renderTurns(turns, tokenData, scrollState = readChatScrollState()) {
    isRenderingChat = true;

    const openDetails = {};
    chatInner.querySelectorAll('details.turn-tools[open]').forEach(function (el) {
      var card = el.closest('.turn-card[data-scroll-key]');
      if (card) openDetails[card.dataset.scrollKey] = true;
    });

    chatInner.innerHTML = '';
    const tokenQueues = tokenData && tokenData.byKey ? tokenData.byKey : null;
    const tokenQueueOffsets = {};
    turns.forEach((turn, turnIdx) => {
      const card = document.createElement('div');
      card.className = 'turn-card';

      const header = document.createElement('div');
      header.className = 'turn-header';

      const avatarClass = avatarClassForAuthor(turn.author);
      const hasAgentIcon = DEFAULT_AGENT_IDS.includes(avatarClass) && avatarClass !== 'hermes';
      const avatar = document.createElement(hasAgentIcon ? 'img' : 'span');
      avatar.className = `turn-avatar ${avatarClass}`;
      if (avatar.tagName === 'IMG') {
        avatar.src = `/icons/${avatarClass}.png`;
        avatar.alt = displayAuthor(turn.author);
      } else {
        const shown = displayAuthor(turn.author);
        avatar.textContent = shown[0].toUpperCase();
      }

      const authorSpan = document.createElement('span');
      authorSpan.className = `turn-author ${avatarClass}`;
      authorSpan.textContent = `@${displayAuthor(turn.author)}`;
      if (displayAuthor(turn.author) !== turn.author) {
        authorSpan.title = turn.author;
      }

      const timeSpan = document.createElement('span');
      timeSpan.className = 'turn-time';
      timeSpan.textContent = displayTurnTime(turn.time);
      if (timeSpan.textContent !== turn.time) {
        timeSpan.title = turn.time;
      }

      header.appendChild(avatar);
      header.appendChild(authorSpan);
      header.appendChild(timeSpan);

      let td = tokenData && !tokenQueues ? tokenData[turnIdx] : null;
      if (tokenQueues) {
        const key = turnEventKey(turn.author, turn.time);
        const queue = tokenQueues[key] || [];
        const offset = tokenQueueOffsets[key] || 0;
        td = queue[offset] || null;
        if (td) tokenQueueOffsets[key] = offset + 1;
      }
      if (td && Number.isInteger(td.event_line_idx)) {
        turn.event_line_idx = td.event_line_idx;
      }
      card.dataset.scrollKey = turnScrollKey(turn, turnIdx);
      if (td && td.metadata && td.metadata.dispatch_mode) {
        const modeBadge = document.createElement('span');
        modeBadge.className = `turn-mode-badge ${td.metadata.dispatch_mode}`;
        modeBadge.textContent = td.metadata.dispatch_mode === 'queued' ? 'Queued' : 'Live';
        modeBadge.title = td.metadata.dispatch_mode === 'queued'
          ? 'Click to start this queued turn immediately.'
          : 'Mentioned agents were started immediately when possible.';
        if (
          turn.author === 'you'
          && td.metadata.dispatch_mode === 'queued'
          && matchingDispatchesForTurn(turn).queued.length
        ) {
          modeBadge.type = 'button';
          modeBadge.tabIndex = 0;
          modeBadge.setAttribute('role', 'button');
          modeBadge.addEventListener('click', () => promoteTurnDispatch(turn, turnIdx));
          modeBadge.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              promoteTurnDispatch(turn, turnIdx);
            }
          });
        }
        header.appendChild(modeBadge);
      }
      const dispatchMatches = turn.author === 'you' ? matchingDispatchesForTurn(turn) : null;
      let inferredStatus = null;
      if (dispatchMatches && dispatchMatches.active.length) {
        inferredStatus = 'running';
      } else if (dispatchMatches && dispatchMatches.queued.length) {
        inferredStatus = 'queued';
      } else if (td && td.metadata && td.metadata.dispatch_mode) {
        inferredStatus = agentMentions(turn.body).length ? 'completed' : 'received';
      }
      const explicitStatus = td && td.metadata && td.metadata.dispatch_status;
      const shownStatus = explicitStatus && explicitStatus !== 'completed' ? explicitStatus : inferredStatus;
      if (shownStatus === 'running') card.classList.add('running');
      if (shownStatus) {
        const statusBadge = document.createElement('span');
        statusBadge.className = `turn-status-badge ${shownStatus}`;
        statusBadge.textContent = shownStatus === 'running'
          ? 'Running'
          : shownStatus === 'queued'
            ? 'Waiting'
          : shownStatus === 'cancelled'
            ? 'Cancelled'
          : shownStatus === 'failed'
            ? 'Failed'
          : shownStatus === 'completed'
            ? 'Completed'
          : 'Received';
        statusBadge.title = shownStatus === 'completed'
          ? 'This turn has no active or queued agent dispatches.'
          : shownStatus === 'received'
            ? 'Message received; no agent dispatch is waiting.'
            : 'Dispatch status.';
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
      const resetBtn = document.createElement('button');
      resetBtn.type = 'button';
      resetBtn.textContent = 'Reset discussion to here';
      resetBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        moreMenu.classList.add('hidden');
        openTurnMenu = null;
        await resetDiscussionToTurn(turn, turnIdx);
      });
      moreMenu.appendChild(resetBtn);
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
      if (td && shouldShowRunDetails(turn, td)) {
        card.appendChild(renderRunDetails(turn, td));
      }
      chatInner.appendChild(card);
    });

    for (var _i = 0; _i < chatInner.children.length; _i++) {
      var card = chatInner.children[_i];
      var key = card.dataset && card.dataset.scrollKey;
      if (key && openDetails[key]) {
        var details = card.querySelector('details.turn-tools');
        if (details) details.open = true;
      }
    }

    restoreChatScrollState(scrollState);
    saveActiveScrollState(true);

    requestAnimationFrame(() => {
      isRenderingChat = false;
    });
  }

  function shouldShowRunDetails(turn, tokenInfo) {
    if (!tokenInfo || turn.author === 'you') return false;
    const memory = tokenInfo.agent_memory || {};
    return Boolean((tokenInfo.tool_calls && tokenInfo.tool_calls.length) || memory.exists || memory.text);
  }

  function renderRunDetails(turn, tokenInfo) {
    const wrap = document.createElement('details');
    wrap.className = 'turn-tools';

    const summary = document.createElement('summary');
    summary.className = 'turn-tools-summary';
    const toolCalls = Array.isArray(tokenInfo.tool_calls) ? tokenInfo.tool_calls : [];
    const memory = tokenInfo.agent_memory || {};
    const count = document.createElement('span');
    count.className = 'turn-tools-count';
    count.textContent = String(toolCalls.length);
    summary.appendChild(count);
    const parts = [];
    parts.push(toolCalls.length + ' tool ' + (toolCalls.length === 1 ? 'action' : 'actions'));
    if (memory.exists || memory.text) parts.push('private memory');
    summary.appendChild(document.createTextNode(parts.join(' + ')));
    wrap.appendChild(summary);

    if (toolCalls.length) {
      wrap.appendChild(renderToolProvenance(toolCalls));
      wrap.appendChild(renderToolJson(toolCalls));
    }
    if (memory.exists || memory.text) {
      wrap.appendChild(renderAgentMemory(turn.author, memory));
    }
    return wrap;
  }

  function renderToolProvenance(toolCalls) {
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
    return list;
  }

  function renderToolJson(toolCalls) {
    const details = document.createElement('details');
    details.className = 'turn-detail-block';
    const summary = document.createElement('summary');
    summary.textContent = 'Raw tool calls';
    details.appendChild(summary);
    const pre = document.createElement('pre');
    pre.className = 'turn-detail-pre';
    pre.textContent = JSON.stringify(toolCalls, null, 2);
    details.appendChild(pre);
    return details;
  }

  function renderAgentMemory(author, memory) {
    const details = document.createElement('details');
    details.className = 'turn-detail-block';
    const summary = document.createElement('summary');
    summary.textContent = '@' + displayAuthor(author) + ' private memory';
    if (memory.path) summary.title = memory.path;
    details.appendChild(summary);

    if (memory.path) {
      const path = document.createElement('div');
      path.className = 'turn-memory-path';
      path.textContent = memory.path + (memory.truncated ? ' (tail shown)' : '');
      details.appendChild(path);
    }

    const pre = document.createElement('pre');
    pre.className = 'turn-detail-pre turn-memory-pre';
    pre.textContent = memory.text || '(no private memory written yet)';
    details.appendChild(pre);
    return details;
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

  function mentionEntries(includeUser) {
    const aliases = currentAgentAliases();
    const entries = [];
    for (const id of AGENT_IDS) {
      entries.push([id, id]);
      const alias = String(aliases[id] || '').trim().replace(/^@/, '').toLowerCase();
      if (alias && alias !== id) entries.push([alias, id]);
    }
    if (includeUser) {
      const userAlias = String((latestAgents.you && latestAgents.you.alias) || 'you').trim().replace(/^@/, '');
      entries.push(['you', 'you']);
      if (userAlias && userAlias.toLowerCase() !== 'you') entries.push([userAlias.toLowerCase(), 'you']);
    }
    entries.sort((a, b) => b[0].length - a[0].length);
    return entries;
  }

  function mentionRegex(includeUser) {
    const names = mentionEntries(includeUser).map(([name]) => name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    return new RegExp('(?<![\\\\\'"\\u2018\\u2019\\u201c\\u201d])@(' + names.join('|') + ')(?!\\w)', 'gi');
  }

  function agentMentions(text) {
    const matches = [];
    const seen = new Set();
    const withoutCodeBlocks = String(text || '').replace(/```[\s\S]*?```/g, ' ');
    const withoutInlineCode = withoutCodeBlocks.replace(/`[^`\n]*`/g, ' ');
    const aliasToAgent = Object.fromEntries(mentionEntries(false));
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
        compacting: Boolean(status.compacting),
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
    const mentionRE = mentionRegex(true);
    const aliasToAgent = Object.fromEntries(mentionEntries(true));
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
    const knownAgents = AGENT_IDS.join('|');
    const tokenRe = new RegExp(
      '```[\\s\\S]*?```|'
      + '``[^`\\n]*(?:`(?!`)[^`\\n]*)*``|'
      + '`[^`\\n`]*`|'
      + '@(?:' + knownAgents + '|[A-Za-z][\\w-]*|\\.[\\w-]+(?:\\.[\\w-]+)+)|'
      + '(?:[a-zA-Z]:[\\\\/]|\\/|\\.\\.?[\\\\/])?[\\w.\\-\\\\/]+[\\\\/][\\w.\\-\\\\/]*\\.[a-zA-Z]{1,8}',
      'g'
    );
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

  function syncComposerHeight() {
    const shouldStick = composerStickToBottom || msgInput.selectionEnd >= msgInput.value.length;
    msgInput.style.height = 'auto';
    const nextHeight = Math.min(Math.max(msgInput.scrollHeight, 64), 220);
    msgInput.style.height = nextHeight + 'px';
    msgHighlights.style.height = nextHeight + 'px';
    if (shouldStick) {
      msgInput.scrollTop = msgInput.scrollHeight;
    }
    msgHighlights.scrollTop = msgInput.scrollTop;
    msgHighlights.scrollLeft = msgInput.scrollLeft;
    composerStickToBottom = false;
  }

  function refreshComposer() {
    syncComposerHeight();
    updateComposerHighlights();
  }

  function ensureComposerBottomVisible() {
    requestAnimationFrame(() => {
      if (composerStickToBottom || msgInput.selectionEnd >= msgInput.value.length) {
        msgInput.scrollTop = msgInput.scrollHeight;
        msgHighlights.scrollTop = msgInput.scrollTop;
        composerStickToBottom = false;
      }
    });
  }

  function replaceComposerRange(start, end, text) {
    msgInput.focus();
    msgInput.setSelectionRange(start, end);
    const usedNativeInsert = document.execCommand && document.execCommand('insertText', false, text);
    if (!usedNativeInsert) {
      msgInput.setRangeText(text, start, end, 'end');
      msgInput.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  function insertAtCursor(text) {
    const value = msgInput.value;
    const start = msgInput.selectionStart || 0;
    const end = msgInput.selectionEnd || start;
    const prefix = start > 0 && value[start - 1] !== '\n' ? '\n' : '';
    const suffix = end < value.length && value[end] !== '\n' ? '\n' : '';
    const inserted = prefix + text + suffix;
    replaceComposerRange(start, end, inserted);
    refreshComposer();
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
    if (!projectRoot) return 'pathless';
    const parts = projectRoot.replaceAll('\\', '/').split('/').filter(Boolean);
    return parts[parts.length - 1] || projectRoot;
  }

  function normalizeHexColor(value, fallback) {
    const color = String(value || '').trim();
    if (/^#[0-9a-fA-F]{6}$/.test(color)) return color.toLowerCase();
    if (/^#[0-9a-fA-F]{3}$/.test(color)) {
      return '#' + color.slice(1).split('').map((ch) => ch + ch).join('').toLowerCase();
    }
    return fallback;
  }

  function colorFor(id, agents = latestAgents) {
    if (id === 'primary') {
      const ui = agents._ui || {};
      return normalizeHexColor(ui.primary, DEFAULT_COLORS.primary);
    }
    const info = agents[id] || {};
    return normalizeHexColor(info.color, DEFAULT_COLORS[id] || '#ffffff');
  }

  function applyAgentColors(agents) {
    const root = document.documentElement;
    root.style.setProperty('--accent', colorFor('primary', agents));
    for (const id of getColorIds()) {
      root.style.setProperty(`--${id}`, colorFor(id, agents));
    }
  }

  function closeMobileDrawers() {
    document.body.classList.remove('mobile-sessions-open');
    mobileDrawerBackdrop.classList.add('hidden');
  }

  function openMobileSessions() {
    configPanel.classList.add('hidden');
    document.body.classList.add('mobile-sessions-open');
    mobileDrawerBackdrop.classList.remove('hidden');
  }

  function renderSessions() {
    if (renamingSessionId) return;
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
      project.title = session.project_root || session.working_root || '';
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
    renamingSessionId = sessionId;
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

    let finishing = false;
    const finish = async () => {
      if (finishing) return;
      finishing = true;
      const newName = input.value.trim();
      renamingSessionId = null;
      if (newName && newName !== currentName) {
        await renameSession(sessionId, newName);
      } else {
        renderSessions();
      }
    };

    input.addEventListener('blur', finish);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      if (e.key === 'Escape') {
        e.preventDefault();
        renamingSessionId = null;
        finishing = true;
        renderSessions();
      }
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
    if (!sessionId) return;
    closeMobileDrawers();
    if (sessionId === activeSessionId) return;
    saveActiveScrollState();
    await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/activate`, { method: 'POST' });
    activeSessionId = sessionId;
    latestAgentsMd = null;
    latestTurns = [];
    latestTokenData = null;
    chatInner.innerHTML = '';
    msgInput.value = '';
    const savedState = sessionScrollStates.get(sessionId);
    userScrolledUp = Boolean(savedState && !savedState.atBottom);
    renderSessions();
    connectWS();
    await Promise.all([fetchChat(), fetchStatus(), fetchTrace(), fetchAgentsMd()]);
    refreshPromptPreview();
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
    latestTurns = [];
    latestTokenData = null;
    chatInner.innerHTML = '';
    markActiveScrollAtBottom();
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
    latestTurns = [];
    latestTokenData = null;
    chatInner.innerHTML = '';
    markActiveScrollAtBottom();
    renderSessions();
    connectWS();
    await Promise.all([fetchChat(), fetchStatus(), fetchTrace(), fetchAgentsMd()]);
    refreshPromptPreview();
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
    return session.project_root || session.working_root || '';
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
      let turns = parseChatMD(chatData.text || '');
      let tokenData = null;
      if (eventsRes.ok) {
        const eventsData = await eventsRes.json();
        turns = filterTurnsByEvents(turns, eventsData.turns);
        const td = {};
        const byKey = {};
        eventsData.turns.forEach((evt, i) => {
          td[i] = evt;
          const key = turnEventKey(evt.author, evt.display_ts);
          if (!byKey[key]) byKey[key] = [];
          byKey[key].push(evt);
        });
        if (Object.keys(byKey).length > 0) td.byKey = byKey;
        if (Object.keys(td).length > 0) tokenData = td;
      }
      latestTurns = turns;
      latestTokenData = tokenData;
      renderTurns(turns, tokenData, scrollStateForRender(targetSessionId));
      loadSessions().catch((e) => console.error('Failed to refresh sessions:', e));
      fetchStatus().catch((e) => console.error('Failed to refresh status:', e));
    } catch (e) {
      console.error('Failed to fetch chat:', e);
    }
  }

  function scheduleFetchChat() {
    chatFetchQueued = true;
    if (chatFetchFrame !== null) return;
    chatFetchFrame = requestAnimationFrame(() => {
      chatFetchFrame = null;
      if (!chatFetchQueued) return;
      chatFetchQueued = false;
      fetchChat();
    });
  }

  async function sendMessage(dispatchMode = 'parallel') {
    if (!activeSessionId) return;
    const text = resolveQuickReply(msgInput.value.trim());
    if (!text) return;
    if (isCompactingStatus()) {
      window.alert('Council is compacting. Wait for compaction to finish before sending another message.');
      return;
    }

    if (text.startsWith('/')) {
      const cmdName = text.split(/\s+/)[0].slice(1).toLowerCase();
      const cmdDef = COMMANDS.find(c => c.name === cmdName);
      if (cmdDef) {
        msgInput.value = '';
        refreshComposer();
        cmdDef.action();
        return;
      }
    }

    msgInput.value = '';
    refreshComposer();
    markActiveScrollAtBottom();
    try {
      const res = await fetch(sessionApi('/send'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, dispatch_mode: dispatchMode }),
      });
      if (!res.ok) {
        const err = await res.json();
        if (res.status === 409 && err.error) {
          window.alert(err.error);
        }
        console.error('Send failed:', err.error);
        return;
      }
      await fetchChat();
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
      'This will summarize older turns into an authoritative shared summary. Council will refuse if agents are still thinking or queued, and sending is blocked until compaction finishes.',
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
      'Clear session context?',
      'This archives and clears the visible chat, compacted summaries, queued work, running work, and per-agent prompt memory for this session.',
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

  async function resetTokenStats() {
    if (!activeSessionId) return;
    showConfirm(
      'Reset token stats?',
      'This resets the session token counters shown in the top bar. Chat history, events, and agent memory are kept. Council refuses while agents are running or queued.',
      async () => {
        try {
          const res = await fetch(sessionApi('/reset_token_stats'), { method: 'POST' });
          if (!res.ok) {
            const err = await res.json();
            window.alert(err.error || res.statusText || 'Reset token stats failed');
            return;
          }
          await fetchStatus();
          await fetchGlobalLog();
        } catch (e) {
          console.error('Reset token stats failed:', e);
        }
      }
    );
  }

  async function resetAgentTokenStats(agent) {
    if (!activeSessionId) return;
    const name = displayAuthor(agent);
    try {
      const res = await fetch(sessionApi('/reset_token_stats') + '?agent=' + encodeURIComponent(agent), { method: 'POST' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        window.alert(err.error || 'Reset token stats failed');
        return;
      }
      await fetchStatus();
      await fetchGlobalLog();
    } catch (e) {
      console.error('Reset agent token stats failed:', e);
    }
  }

  function findMentionedAgents(text) {
    const aliases = currentAgentAliases();
    const found = [];
    for (const id of AGENT_IDS) {
      const alias = String(aliases[id] || '').trim().replace(/^@/, '').toLowerCase();
      const pattern = new RegExp('(?<![\\\\\'\"\\u2018\\u2019\\u201c\\u201d])@(' + escapeRegex(id) + (alias && alias !== id ? '|' + escapeRegex(alias) : '') + ')(?!\\w)', 'gi');
      if (pattern.test(text || '')) {
        found.push(id);
      }
    }
    return found;
  }

  function escapeRegex(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  async function fetchPromptPreview() {
    if (!activeSessionId) {
      renderAgentPromptBar(null);
      return;
    }
    const agents = AGENT_IDS;
    const draft = msgInput.value.trim();
    if (draft === lastPreviewDraft && lastPreviewData) {
      renderAgentPromptBar(lastPreviewData);
      return;
    }
    lastPreviewDraft = draft;
    lastPreviewAgents = agents;
    lastSentPreviewAgents = JSON.stringify(agents);
    try {
      const res = await fetch(sessionApi('/prompt_preview'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ draft_text: draft, agents }),
      });
      if (!res.ok) return;
      const data = await res.json();
      if (draft !== msgInput.value.trim()) return;
      lastPreviewData = data;
      renderAgentPromptBar(data);
    } catch (_) {
      renderAgentPromptBar(null);
    }
  }

  function refreshPromptPreview() {
    lastPreviewDraft = '';
    lastPreviewData = null;
    schedulePromptPreview();
  }

  function schedulePromptPreview() {
    if (previewTimer !== null) clearTimeout(previewTimer);
    previewTimer = setTimeout(() => {
      previewTimer = null;
      fetchPromptPreview();
    }, 250);
  }

  function renderAgentPromptBar(data) {
    agentPromptBarEl.innerHTML = '';
    if (!data || !Object.keys(data).length) {
      for (const id of AGENT_IDS) {
        appendAgentPromptRow(id, null);
      }
      return;
    }
    let sharedChat = 0;
    let sharedMsg = 0;
    for (const id of AGENT_IDS) {
      const info = data[id];
      if (info) {
        sharedChat = info.chat_tail || 0;
        sharedMsg = info.user_msg || 0;
        break;
      }
    }
    if (sharedChat || sharedMsg) {
      const sharedRow = document.createElement('span');
      sharedRow.className = 'agent-prompt-row shared';
      const sharedName = document.createElement('span');
      sharedName.className = 'agent-prompt-name';
      sharedName.textContent = 'shared';
      sharedRow.appendChild(sharedName);
      const sharedCount = document.createElement('span');
      sharedCount.className = 'agent-prompt-count';
      const parts = [];
      if (sharedChat) parts.push('chat ' + (formatTokenCount(sharedChat) || String(sharedChat)));
      if (sharedMsg) parts.push('msg ' + (formatTokenCount(sharedMsg) || String(sharedMsg)));
      sharedCount.textContent = parts.join(' + ');
      sharedRow.appendChild(sharedCount);
      agentPromptBarEl.appendChild(sharedRow);
    }
    for (const id of AGENT_IDS) {
      const info = data[id] || null;
      appendAgentPromptRow(id, info);
    }
  }

  function appendAgentPromptRow(id, info) {
    const row = document.createElement('span');
    row.className = 'agent-prompt-row ' + id;

    const name = document.createElement('span');
    name.className = 'agent-prompt-name';
    name.textContent = '@' + displayAuthor(id);
    name.title = 'Click to @mention ' + displayAuthor(id);
    row.appendChild(name);

    if (info) {
      const barWrap = document.createElement('span');
      barWrap.className = 'agent-prompt-bar-wrap';
      const bar = document.createElement('span');
      bar.className = 'agent-prompt-bar-fill';
      const total = info.total || 0;
      const ctxWindow = info.context_window || 200000;
      const pct = ctxWindow ? Math.min(100, Math.round((total / ctxWindow) * 100)) : 0;
      bar.style.width = Math.max(2, pct) + '%';
      if (pct > 80) bar.classList.add('danger');
      else if (pct > 50) bar.classList.add('warning');
      barWrap.appendChild(bar);

      const count = document.createElement('span');
      count.className = 'agent-prompt-count';
      count.textContent = formatTokenCount(total) || String(total);

      const breakdown = document.createElement('span');
      breakdown.className = 'agent-prompt-breakdown';
      const mem = formatTokenCount(info.private_memory || 0) || '0';
      breakdown.textContent = 'mem ' + mem;

      row.appendChild(barWrap);
      row.appendChild(count);
      row.appendChild(breakdown);
    }

    agentPromptBarEl.appendChild(row);
  }

  function turnIdentity(turn, turnIdx) {
    return {
      index: turnIdx,
      event_line_idx: turn.event_line_idx,
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

  async function resetDiscussionToTurn(turn, turnIdx) {
    if (!activeSessionId) return;
    if (
      latestStatus
      && (
        latestStatus.busy
        || (latestStatus.active_dispatches || []).length
        || (latestStatus.dispatch_queue || []).length
      )
    ) {
      window.alert('Cannot reset while agents are running or queued.');
      return;
    }
    showConfirm(
      'Reset discussion to here?',
      'Archive and remove every later chat turn, queued state, and per-agent prompt memory after this point. Council refuses while an agent is running or queued.',
      async () => {
        try {
          const res = await fetch(sessionApi('/reset_to_turn'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(turnIdentity(turn, turnIdx)),
          });
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            window.alert(err.error || res.statusText || 'Reset failed');
            return;
          }
          markActiveScrollAtBottom();
          await Promise.all([fetchChat(), fetchStatus(), fetchTrace()]);
        } catch (e) {
          console.error('Reset failed:', e);
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

  async function promoteTurnDispatch(turn, turnIdx) {
    if (!activeSessionId || !turn) return;
    try {
      const res = await fetch(sessionApi('/promote_turn'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          index: turnIdx,
          event_line_idx: turn.event_line_idx,
          author: turn.author,
          display_ts: turn.time,
          original_text: turn.body || '',
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        console.error('Promote queued turn failed:', err.error || res.statusText);
      }
      await Promise.all([fetchChat(), fetchStatus()]);
    } catch (e) {
      console.error('Promote queued turn failed:', e);
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

  async function fetchGlobalLog() {
    try {
      const res = await fetch('/api/global-log');
      const data = await res.json();
      latestGlobalLogEvents = data.events || [];
      renderGlobalLog(latestGlobalLogEvents);
    } catch (_) {}
  }

  function renderTrace(events) {
    traceList.innerHTML = '';
    for (const event of events) {
      appendTraceEvent(event);
    }
  }

  function renderGlobalLog(events) {
    if (!globalLogList) return;
    globalLogList.innerHTML = '';
    for (const event of events) {
      const item = document.createElement('div');
      item.className = 'global-log-item';
      const meta = document.createElement('div');
      meta.className = 'global-log-meta';
      meta.textContent = event.time || '';
      const message = document.createElement('div');
      message.className = 'global-log-message';
      const count = Number(event.count || 1);
      message.textContent = `${cleanTraceText(event.message)}${count > 1 ? ` x${count}` : ''}`;
      item.appendChild(meta);
      item.appendChild(message);
      if (event.detail) {
        const detail = document.createElement('div');
        detail.className = 'global-log-detail';
        detail.textContent = cleanTraceText(event.detail);
        item.appendChild(detail);
      }
      globalLogList.appendChild(item);
    }
    globalLogList.scrollTop = globalLogList.scrollHeight;
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
    const count = Number(event.count || 1);
    message.textContent = `${cleanTraceText(event.message)}${count > 1 ? ` x${count}` : ''}`;

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
          scheduleFetchChat();
        } else if (msg.type === 'status') {
          latestStatus = msg;
          updateStatus(msg);
          loadSessions().catch((e) => console.error('Failed to refresh sessions:', e));
        } else if (msg.type === 'trace_update') {
          const traceEvent = msg.event || {};
          const last = latestTraceEvents[latestTraceEvents.length - 1];
          if (
            last
            && last.agent === traceEvent.agent
            && last.message === traceEvent.message
            && last.detail === traceEvent.detail
          ) {
            latestTraceEvents[latestTraceEvents.length - 1] = traceEvent;
          } else {
            latestTraceEvents.push(traceEvent);
          }
          latestTraceEvents = latestTraceEvents.slice(-100);
          renderTrace(latestTraceEvents);
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
    const dynIds = Object.keys(latestAgents).filter(k => k !== '_ui' && k !== 'you');
    if (dynIds.length) AGENT_IDS = dynIds;
    latestGlobalAgents = data.global_agents || latestGlobalAgents || {};
    latestDispatch = data.dispatch || {};
    latestGlobalDispatch = data.global_dispatch || latestGlobalDispatch || {};
    applyAgentColors(latestAgents);
    renderAgentCards(latestAgents, data.token_totals);
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
        statusIndicator.innerHTML = '&#9679; @' + displayAuthor(agentName) + ' thinking&hellip;';
      }
      cancelBtn.classList.remove('hidden');
    } else {
      statusIndicator.className = 'idle';
      statusIndicator.innerHTML = '&#9679; idle';
      cancelBtn.classList.add('hidden');
    }
    const compacting = isCompactingStatus(data);
    sendNowBtn.disabled = compacting;
    sendBtn.disabled = compacting;
    msgInput.setAttribute('aria-disabled', compacting ? 'true' : 'false');
    sendNowBtn.title = compacting
      ? 'Wait for compaction to finish before sending'
      : 'Send and start mentioned agents immediately';
    sendBtn.title = compacting
      ? 'Wait for compaction to finish before queueing'
      : 'Queue (Ctrl+Enter)';
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
    const usedPct = Number(compact.used_percent || 0);
    const suggestPct = Number(compact.suggest_used_percent || 80);
    const shouldCompact = Boolean(compact.should_compact);
    const warnCompact = Boolean(shouldCompact || compact.warning);
    compactStatusEl.className = compact.over_threshold
      ? 'danger'
      : (warnCompact ? 'warning' : '');
    compactStatusEl.textContent = compact.over_threshold
      ? 'compact due'
      : (warnCompact ? `compact suggested ${usedPct}%` : `compact ${remainingPct}% left`);
    compactStatusEl.title = warnCompact
      ? `Suggested at ${suggestPct}% used; currently ${usedPct}% used; ${remaining} lines until auto compact (${compact.line_count}/${compact.threshold_lines})`
      : `${remaining} lines until auto compact (${compact.line_count}/${compact.threshold_lines})`;
  }

  function dispatchLabel(item) {
    if (!item || !item.agent) return '@?';
    const source = item.source === 'user' ? 'from you' : 'from agent';
    return '@' + displayAuthor(item.agent) + ' ' + source;
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

  function renderAgentCards(agents, totals) {
    const order = AGENT_IDS;
    agentModelsEl.innerHTML = '';
    for (const id of order) {
      const info = agents[id];
      if (!info) continue;
      const card = document.createElement('div');
      card.className = `agent-card ${id}`;

      const title = document.createElement('div');
      title.className = 'agent-card-title';
      title.textContent = '@' + (info.alias || id);

      if (!DEFAULT_AGENT_IDS.includes(id)) {
        const color = info.color || colorForAny(id, {});
        card.style.borderColor = color + '66';
        title.style.color = color;
      }

      const sub = document.createElement('div');
      sub.className = 'agent-card-sub';
      const modelName = info.model || info.runtime || 'default';
      const effort = info.effort ? ' \u00b7 ' + info.effort : '';
      const key = info.api_key_saved ? ' \u00b7 \uD83D\uDD11' : '';
      sub.textContent = modelName + effort + key;
      sub.title = `${info.provider || ''}${info.runtime ? ' - ' + info.runtime : ''}${info.note ? ' - ' + info.note : ''}`;

      const tokenRow = document.createElement('div');
      tokenRow.className = 'agent-card-tokens';
      const tokenItem = totals && totals[id];
      const tokenCount = tokenItem
        ? (formatTokenCount(tokenItem.total_tokens) || '~' + (formatTokenCount(tokenItem.total_tokens_est) || '0'))
        : '\u2014';
      tokenRow.textContent = tokenCount + ' session';

      const resetBtn = document.createElement('button');
      resetBtn.type = 'button';
      resetBtn.className = 'agent-card-reset';
      resetBtn.title = 'Reset token counter for @' + displayAuthor(id);
      resetBtn.innerHTML = '\u27F3';
      resetBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        resetAgentTokenStats(id);
      });
      tokenRow.appendChild(resetBtn);

      card.appendChild(title);
      card.appendChild(sub);
      card.appendChild(tokenRow);
      agentModelsEl.appendChild(card);
    }
  }

  function updateAgentButtons() {
    const aliases = AGENT_IDS.map((id) => '@' + ((latestAgents[id] && latestAgents[id].alias) || id));
    msgInput.placeholder = aliases.join(' / ') + ' activates that agent. Use plain names when not summoning.';
    const rows = getAgentPromptRows();
    if (!rows.length) {
      renderAgentPromptBar(lastPreviewData);
    } else {
      rows.forEach((el) => {
        const row = el.closest('.agent-prompt-row');
        const id = row ? [...row.classList].find((c) => AGENT_IDS.includes(c)) : null;
        if (id && latestAgents[id]) {
          el.textContent = '@' + (latestAgents[id].alias || id);
        }
      });
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
    select.value = value || (agentId === 'claude' ? 'claude_cli' : agentId === 'codex' ? 'codex_cli' : agentId === 'deepseek' ? 'opencode' : agentId === 'hermes' ? 'hermes_api' : 'hermes_api');
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

  function makeColorInput(id, value) {
    const input = document.createElement('input');
    input.type = 'color';
    input.value = normalizeHexColor(value, DEFAULT_COLORS[id] || '#ffffff');
    input.title = `Set ${id} color`;
    input.addEventListener('input', () => {
      const variable = id === 'primary' ? '--accent' : `--${id}`;
      document.documentElement.style.setProperty(variable, input.value);
    });
    input.addEventListener('change', () => {
      patchConfig({ ui: { colors: { [id]: input.value } } });
    });
    return input;
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

  function makeCollapsibleSection(title, bodyChildren) {
    const wrapper = document.createElement('div');
    wrapper.className = 'config-collapsible';

    const header = document.createElement('div');
    header.className = 'config-collapsible-header';

    const chevron = document.createElement('span');
    chevron.className = 'config-chevron';
    chevron.textContent = '\u25B6';

    const titleEl = document.createElement('span');
    titleEl.className = 'config-collapsible-title';
    titleEl.textContent = title;

    header.appendChild(chevron);
    header.appendChild(titleEl);

    const body = document.createElement('div');
    body.className = 'config-collapsible-body collapsed';

    for (const child of bodyChildren) {
      body.appendChild(child);
    }

    header.addEventListener('click', () => {
      wrapper.classList.toggle('open');
      body.classList.toggle('collapsed');
    });

    wrapper.appendChild(header);
    wrapper.appendChild(body);
    return wrapper;
  }

  function renderConfig(agents, dispatchCfg) {
    if (configPanel.classList.contains('hidden')) return;
    const order = AGENT_IDS;
    configGrid.innerHTML = '';

    const themeSection = document.createElement('div');
    themeSection.className = 'config-section config-theme-section';
    const primaryColorWrap = document.createElement('label');
    primaryColorWrap.textContent = 'Primary';
    primaryColorWrap.appendChild(makeColorInput('primary', colorFor('primary', agents)));
    themeSection.appendChild(primaryColorWrap);

    const resetThemeBtn = document.createElement('button');
    resetThemeBtn.type = 'button';
    resetThemeBtn.className = 'config-panel-btn';
    resetThemeBtn.textContent = 'Reset primary';
    resetThemeBtn.addEventListener('click', () => {
      document.documentElement.style.setProperty('--accent', DEFAULT_COLORS.primary);
      patchConfig({ ui: { colors: { primary: DEFAULT_COLORS.primary } } });
    });
    themeSection.appendChild(resetThemeBtn);
    configGrid.appendChild(makeCollapsibleSection('Theme', [themeSection]));

    const userSection = document.createElement('div');
    userSection.className = 'config-section config-user-section';
    const userAliasWrap = document.createElement('label');
    userAliasWrap.textContent = 'Name';
    const userAlias = document.createElement('input');
    userAlias.type = 'text';
    userAlias.value = (agents.you && agents.you.alias) || 'you';
    userAlias.placeholder = 'Zan';
    userAlias.addEventListener('change', () => {
      patchConfig({ aliases: { you: userAlias.value.trim().replace(/^@/, '') || 'you' } });
    });
    userAliasWrap.appendChild(userAlias);
    userSection.appendChild(userAliasWrap);
    const userColorWrap = document.createElement('label');
    userColorWrap.textContent = 'Color';
    userColorWrap.appendChild(makeColorInput('you', colorFor('you', agents)));
    userSection.appendChild(userColorWrap);
    configGrid.appendChild(makeCollapsibleSection('User display', [userSection]));

    const agentRows = [];
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

      const colorWrap = document.createElement('label');
      colorWrap.textContent = 'Color';
      colorWrap.appendChild(makeColorInput(id, colorFor(id, agents)));

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
      row.appendChild(colorWrap);
      row.appendChild(keyWrap);
      row.appendChild(roleWrap);

      if (info.removable) {
        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'config-panel-btn config-remove-agent-btn';
        removeBtn.textContent = 'Remove';
        removeBtn.title = `Remove ${info.label || id} from this session`;
        removeBtn.addEventListener('click', () => {
          if (confirm(`Remove @${info.alias || id} from this session?`)) {
            fetch(sessionApi(`/agents/${encodeURIComponent(id)}`), { method: 'DELETE' })
              .then(r => r.json())
              .then(d => {
                if (d.ok) {
                  latestAgents = d.agents || {};
                  const dynIds = Object.keys(latestAgents).filter(k => k !== '_ui' && k !== 'you');
                  if (dynIds.length) AGENT_IDS = dynIds;
                  renderConfig(latestAgents, latestDispatch || {});
                  renderAgentCards(latestAgents, null);
                }
              })
              .catch(() => {});
          }
        });
        row.appendChild(removeBtn);
      }

      agentRows.push(row);
    }

    const addAgentRow = document.createElement('div');
    addAgentRow.className = 'config-row config-add-agent-row';
    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.className = 'config-panel-btn';
    addBtn.textContent = '+ Add Agent';
    addBtn.title = 'Add a new AI agent slot to this session';
    addBtn.addEventListener('click', () => {
      const name = prompt('Enter agent name (lowercase, letters/digits/hyphens):');
      if (!name) return;
      const clean = name.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '');
      if (!clean || !/^[a-z]/.test(clean)) {
        alert('Agent name must start with a letter and use only lowercase letters, digits, hyphens, or underscores.');
        return;
      }
      fetch(sessionApi('/agents'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: clean, provider: 'hermes_api' }),
      })
        .then(r => r.json())
        .then(d => {
          if (d.ok) {
            latestAgents = d.agents || {};
            const dynIds = Object.keys(latestAgents).filter(k => k !== '_ui' && k !== 'you');
            if (dynIds.length) AGENT_IDS = dynIds;
            renderConfig(latestAgents, latestDispatch || {});
            renderAgentCards(latestAgents, null);
          } else {
            alert(d.detail || 'Failed to add agent.');
          }
        })
        .catch(err => alert('Error: ' + err.message));
    });
    addAgentRow.appendChild(addBtn);
    agentRows.push(addAgentRow);

    configGrid.appendChild(makeCollapsibleSection('Agents', agentRows));

    const flashInfo = agents.deepseek || {};
    const flashSection = document.createElement('div');
    flashSection.className = 'config-section config-flash-section';
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
    configGrid.appendChild(makeCollapsibleSection('Deepseek Flash summarizer', [flashSection]));

    const dispatchSection = document.createElement('div');
    dispatchSection.className = 'config-section';

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
    configGrid.appendChild(makeCollapsibleSection('Dispatch limits', [dispatchSection]));

    const hermesDispatch = dispatchCfg && dispatchCfg.hermes || {};
    const hermesSection = document.createElement('div');
    hermesSection.className = 'config-section';

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
    configGrid.appendChild(makeCollapsibleSection('Hermes/API bridge', [hermesSection]));

    const attachmentSection = document.createElement('div');
    attachmentSection.className = 'config-section config-attachment-section';

    const attachmentPolicies = dispatchCfg && dispatchCfg.attachments || {};
    for (const id of order) {
      const info = agents[id];
      if (!info) continue;
      const wrap = document.createElement('label');
      wrap.textContent = `@${info.alias || id}`;
      wrap.appendChild(makeAttachmentPolicySelect(id, attachmentPolicies[id]));
      attachmentSection.appendChild(wrap);
    }
    configGrid.appendChild(makeCollapsibleSection('Attachment prompts', [attachmentSection]));

    const agentsDocSection = document.createElement('div');
    agentsDocSection.className = 'config-section config-agents-md-section';

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

    const agentsDocToggle = document.createElement('button');
    agentsDocToggle.type = 'button';
    agentsDocToggle.className = 'config-panel-btn';
    agentsDocToggle.disabled = globalConfigCheckbox.checked;
    agentsDocToggle.textContent = agentsDocPanelOpen ? 'Close editor' : 'Open editor';
    agentsDocToggle.addEventListener('click', () => {
      agentsDocPanelOpen = !agentsDocPanelOpen;
      renderConfig(
        globalConfigCheckbox.checked ? latestGlobalAgents : latestAgents,
        globalConfigCheckbox.checked ? latestGlobalDispatch : latestDispatch
      );
    });
    agentsDocSection.appendChild(agentsDocToggle);

    const agentsDoc = document.createElement('textarea');
    agentsDoc.rows = 9;
    agentsDoc.spellcheck = false;
    agentsDoc.disabled = globalConfigCheckbox.checked;
    agentsDoc.placeholder = globalConfigCheckbox.checked
      ? 'Switch off global defaults to edit this session instructions.'
      : '# Project instructions for all Council agents';
    agentsDoc.value = globalConfigCheckbox.checked ? '' : ((latestAgentsMd && latestAgentsMd.text) || '');
    agentsDoc.addEventListener('change', () => {
      if (!agentsDoc.disabled) saveAgentsMd(agentsDoc.value);
    });
    if (agentsDocPanelOpen && !globalConfigCheckbox.checked) {
      const agentsDocPanel = document.createElement('div');
      agentsDocPanel.className = 'config-agents-md-panel';
      agentsDocPanel.appendChild(agentsDoc);
      agentsDocSection.appendChild(agentsDocPanel);
    }
    configGrid.appendChild(makeCollapsibleSection('Session AGENTS.md', [agentsDocSection]));
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

  function scrollSelectedACIntoView() {
    const selected = acBox.querySelector('.ac-row.selected');
    if (!selected) return;
    selected.scrollIntoView({ block: 'nearest' });
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
    scrollSelectedACIntoView();
  }

  function applyAC() {
    if (!acState.open || acState.items.length === 0) {
      closeAC();
      return;
    }
    const it = acState.items[acState.selectedIdx];
    const triggerLen = acState.triggerChar === '@@' ? 2 : 1;
    const afterStart = acState.triggerStart + triggerLen + acState.query.length;
    const inserted = acState.triggerChar === '@@' ? it.insert + ' ' : acState.triggerChar + it.insert + ' ';
    replaceComposerRange(acState.triggerStart, afterStart, inserted);
    closeAC();
    msgInput.focus();
    refreshComposer();
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
    refreshComposer();
    ensureComposerBottomVisible();
    schedulePromptPreview();
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
  msgInput.addEventListener('wheel', (e) => {
    if (msgInput.scrollHeight <= msgInput.clientHeight) return;
    const before = msgInput.scrollTop;
    msgInput.scrollTop += e.deltaY;
    if (msgInput.scrollTop !== before) {
      e.preventDefault();
      msgHighlights.scrollTop = msgInput.scrollTop;
    }
  }, { passive: false });
  msgInput.addEventListener('paste', handleImagePaste);
  msgInput.addEventListener('blur', () => {
    setTimeout(closeAC, 150);
  });

  msgInput.addEventListener('keydown', (e) => {
    if (acState.open) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (acState.items.length > 0) {
          acState.selectedIdx = (acState.selectedIdx + 1) % acState.items.length;
          renderAC();
          scrollSelectedACIntoView();
        }
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (acState.items.length > 0) {
          acState.selectedIdx = (acState.selectedIdx - 1 + acState.items.length) % acState.items.length;
          renderAC();
          scrollSelectedACIntoView();
        }
        return;
      }
      if (e.key === 'Tab' || e.key === 'Enter') {
        e.preventDefault();
        if (acState.items.length > 0) {
          applyAC();
        }
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        closeAC();
        return;
      }
    }
    if (e.key === 'Enter') {
      composerStickToBottom = true;
      requestAnimationFrame(() => {
        refreshComposer();
        ensureComposerBottomVisible();
      });
    }
    if (
      ['Backspace', 'Delete'].includes(e.key) &&
      msgInput.selectionEnd >= msgInput.value.length - 1
    ) {
      composerStickToBottom = true;
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
  resetTokenStatsBtn.addEventListener('click', resetTokenStats);
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
    if (e.key === 'Escape') {
      closeMobileDrawers();
    }
    if (e.key === 'Escape' && openTurnMenu) {
      openTurnMenu.classList.add('hidden');
      openTurnMenu = null;
    }
  });
  mobileMenuBtn.addEventListener('click', openMobileSessions);
  mobileDrawerBackdrop.addEventListener('click', () => {
    configPanel.classList.add('hidden');
    closeMobileDrawers();
  });
  configToggleBtn.addEventListener('click', async () => {
    closeMobileDrawers();
    configPanel.classList.toggle('hidden');
    if (!configPanel.classList.contains('hidden') && window.matchMedia('(max-width: 760px)').matches) {
      mobileDrawerBackdrop.classList.remove('hidden');
    } else {
      mobileDrawerBackdrop.classList.add('hidden');
    }
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
  agentPromptBarEl.addEventListener('click', (e) => {
    const nameEl = e.target.closest('.agent-prompt-name');
    if (!nameEl) return;
    const row = nameEl.closest('.agent-prompt-row');
    if (!row) return;
    const id = [...row.classList].find((c) => AGENT_IDS.includes(c));
    if (!id) return;
    const info = latestAgents[id] || {};
    const mention = '@' + (info.alias || id);
    const text = msgInput.value.trim();
    replaceComposerRange(0, msgInput.value.length, text ? `${mention} ${text}` : `${mention} `);
    msgInput.focus();
    msgInput.setSelectionRange(msgInput.value.length, msgInput.value.length);
    refreshComposer();
  });

  newSessionBtn.addEventListener('click', () => {
    const current = activeSession();
    newSessionProject.value = '';
    newSessionProject.placeholder = current && current.project_root
      ? `Project root (optional, current: ${projectShortName(current.project_root)})`
      : 'Project root (optional)';
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
    loadStoredScrollStates();
    refreshComposer();
    await loadSessions();
    renderSessions();
    connectWS();
    await Promise.all([fetchChat(), fetchStatus(), fetchTrace(), fetchAgentsMd(), fetchGlobalLog()]);
    setInterval(() => {
      loadSessions().catch(() => {});
      fetchGlobalLog().catch(() => {});
    }, 3000);
  }

  init();
})();
