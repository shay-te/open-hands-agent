// Kato planning UI client.
// Uses Server-Sent Events (server → browser) and POST endpoints (browser →
// server) instead of WebSockets. Same effective behavior, but reliable on
// Werkzeug's dev server (which mishandles WS upgrades).

(function () {
  'use strict';

  const tabList = document.getElementById('tab-list');
  const emptyState = document.getElementById('empty-state');
  const refreshButton = document.getElementById('refresh-tabs');
  const placeholder = document.getElementById('session-placeholder');
  const detail = document.getElementById('session-detail');
  const headerStatusDot = document.getElementById('session-status-dot');
  const headerTaskId = document.getElementById('session-task-id');
  const headerTaskSummary = document.getElementById('session-task-summary');
  const eventLog = document.getElementById('event-log');
  const messageForm = document.getElementById('message-form');
  const messageInput = document.getElementById('message-input');
  const sendButton = messageForm.querySelector('button[type="submit"]');

  const stopButton = document.getElementById('session-stop');

  const permissionModal = document.getElementById('permission-modal');
  const permissionToolName = document.getElementById('permission-tool-name');
  const permissionFields = document.getElementById('permission-fields');
  const permissionDetail = document.getElementById('permission-detail');
  const permissionRationale = document.getElementById('permission-rationale');
  const permissionAllow = document.getElementById('permission-allow');
  const permissionDeny = document.getElementById('permission-deny');
  const permissionRemember = document.getElementById('permission-remember');
  const permissionRememberTool = document.getElementById('permission-remember-tool');

  let activeStream = null;
  let activeTaskId = null;
  let pendingPermission = null;
  // tool_name -> 'allow' | 'deny' decisions the user marked "don't ask
  // again this session". Cleared when the active tab changes (so a new
  // task gets fresh approval rounds) and on page reload.
  let sessionToolDecisions = {};
  // True while Claude is mid-turn for the active tab. Disables the send
  // button so the user can't queue messages while the agent is working
  // (queueing technically works, but the UX of "can I send now?" matters).
  let activeTurnInFlight = false;
  // Last branch_state event payload for the active tab. When `locked` is
  // true the repo's HEAD doesn't match what this session was started for
  // (kato has moved on to another branch); we hard-disable the send
  // button so chat-driven edits don't land on the wrong branch.
  let activeBranchState = { expected: '', current: '', locked: false };

  refreshButton.addEventListener('click', refreshTabList);
  tabList.addEventListener('click', onTabClick);
  messageForm.addEventListener('submit', onSendMessage);
  messageInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      onSendMessage(event);
    }
  });
  permissionAllow.addEventListener('click', () => respondToPermission(true));
  permissionDeny.addEventListener('click', () => respondToPermission(false));
  if (stopButton) { stopButton.addEventListener('click', stopActiveSession); }

  // Bridge: the React right-pane bundle dispatches `kato:file-clicked`
  // when the user picks a file; we drop the relative path into the chat
  // textarea so the message they're composing references it.
  window.addEventListener('kato:file-clicked', (event) => {
    const path = (event && event.detail && event.detail.path) || '';
    if (!path || !activeTaskId) { return; }
    insertIntoMessageInput(path);
  });

  setInterval(refreshTabList, 5000);
  refreshTabList();

  initStatusBar();
  initRightPaneResizer();
  initNotifications();

  // ----- tab list -----

  async function refreshTabList() {
    try {
      const response = await fetch('/api/sessions', { cache: 'no-store' });
      if (!response.ok) { return; }
      const records = await response.json();
      renderTabList(records);
    } catch (err) {
      console.warn('failed to refresh sessions', err);
    }
  }

  function renderTabList(records) {
    tabList.innerHTML = '';
    if (!records || records.length === 0) {
      emptyState.style.display = '';
      if (activeTaskId) {
        closeActiveStream();
        showPlaceholder();
      }
      return;
    }
    emptyState.style.display = 'none';
    for (const record of records) {
      const li = document.createElement('li');
      li.className = 'tab' + (record.task_id === activeTaskId ? ' active' : '');
      li.dataset.taskId = record.task_id;
      li.innerHTML = `
        <span class="status-dot status-${escapeAttr(record.status || 'active')}"
              title="${escapeAttr(record.status || 'active')}"></span>
        <strong>${escapeHtml(record.task_id)}</strong>
        <p>${escapeHtml(record.task_summary || '')}</p>
      `;
      tabList.appendChild(li);
    }
  }

  function onTabClick(event) {
    const tab = event.target.closest('.tab');
    if (!tab) { return; }
    const taskId = tab.dataset.taskId;
    if (!taskId || taskId === activeTaskId) { return; }
    openSessionTab(taskId, tab);
  }

  function openSessionTab(taskId, tabElement) {
    closeActiveStream();
    activeTaskId = taskId;
    // Reset per-session memory: each tab gets its own scratch.
    sessionToolDecisions = {};
    // Reset branch state — the SSE stream's first message will refresh it.
    activeBranchState = { expected: '', current: '', locked: false };
    // Start idle. The backlog replay will lock us if it ends with an
    // unfinished assistant turn (we set in-flight on assistant events
    // and clear it on result events). Wait-planning sessions sit idle
    // until the user sends, so the default must be enabled.
    setTurnInFlight(false);
    [...tabList.querySelectorAll('.tab')].forEach((el) =>
      el.classList.toggle('active', el === tabElement),
    );
    showDetail(taskId, tabElement);
    eventLog.innerHTML = '';
    appendBubble('system', `Connecting to session for ${taskId}…`);
    openStream(taskId);
    notifyActiveTaskChanged(taskId);
  }

  function notifyActiveTaskChanged(taskId) {
    // Stash the current task on window so a late-mounting subscriber
    // (e.g. the React right-pane bundle) can read it via the published
    // accessor. Without this, a fast tab-click before React's effect
    // attaches drops the event and the right pane stays empty.
    window.__katoActiveTaskId = taskId || '';
    window.dispatchEvent(new CustomEvent('kato:active-task', {
      detail: { taskId: window.__katoActiveTaskId },
    }));
  }

  window.katoGetActiveTaskId = () => window.__katoActiveTaskId || '';

  // ----- right-pane resizer -----

  const RIGHT_PANE_MIN_WIDTH = 220;
  const RIGHT_PANE_MAX_WIDTH = 900;
  const RIGHT_PANE_STORAGE_KEY = 'kato.rightPaneWidth';

  function initRightPaneResizer() {
    const layout = document.getElementById('layout');
    const resizer = document.getElementById('right-pane-resizer');
    const pane = document.getElementById('right-pane');
    if (!layout || !resizer || !pane) { return; }

    const stored = parseInt(localStorage.getItem(RIGHT_PANE_STORAGE_KEY) || '', 10);
    if (Number.isFinite(stored)) {
      layout.style.setProperty('--right-pane-width', `${clampPaneWidth(stored)}px`);
    }

    let startX = 0;
    let startWidth = 0;

    function onMove(event) {
      // Drag toward the LEFT widens the right pane (it's anchored on
      // the right edge of the layout grid).
      const delta = startX - event.clientX;
      const next = clampPaneWidth(startWidth + delta);
      layout.style.setProperty('--right-pane-width', `${next}px`);
    }

    function onUp() {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.classList.remove('kato-resizing');
      const current = parseInt(
        layout.style.getPropertyValue('--right-pane-width'), 10,
      );
      if (Number.isFinite(current)) {
        localStorage.setItem(RIGHT_PANE_STORAGE_KEY, String(current));
      }
    }

    resizer.addEventListener('mousedown', (event) => {
      event.preventDefault();
      startX = event.clientX;
      startWidth = pane.getBoundingClientRect().width;
      document.body.classList.add('kato-resizing');
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  function clampPaneWidth(value) {
    return Math.min(RIGHT_PANE_MAX_WIDTH, Math.max(RIGHT_PANE_MIN_WIDTH, value));
  }

  // ----- browser notifications -----

  const NOTIFICATION_PREF_KEY = 'kato.notifications';
  let notificationsEnabled = false;

  function initNotifications() {
    const button = document.getElementById('notifications-toggle');
    if (!button) { return; }
    if (!('Notification' in window)) {
      button.disabled = true;
      button.title = 'This browser does not support notifications';
      return;
    }
    // Restore the user's last preference, but only if the OS-level
    // permission is still granted — if they've revoked it, we wait for
    // a fresh user gesture before re-prompting.
    if (
      localStorage.getItem(NOTIFICATION_PREF_KEY) === 'on'
      && Notification.permission === 'granted'
    ) {
      notificationsEnabled = true;
    }
    refreshNotificationsButton();
    button.addEventListener('click', toggleNotifications);
  }

  function toggleNotifications() {
    if (notificationsEnabled) {
      notificationsEnabled = false;
      localStorage.setItem(NOTIFICATION_PREF_KEY, 'off');
      refreshNotificationsButton();
      return;
    }
    if (Notification.permission === 'denied') {
      appendBubble('error',
        'Notifications are blocked at the browser/OS level. ' +
        'Allow them in site settings, then reload.');
      return;
    }
    if (Notification.permission === 'default') {
      Notification.requestPermission().then((permission) => {
        if (permission === 'granted') {
          notificationsEnabled = true;
          localStorage.setItem(NOTIFICATION_PREF_KEY, 'on');
          refreshNotificationsButton();
          maybeNotify({
            title: 'Kato notifications enabled',
            body: "You'll be pinged when tasks start, complete, or need attention.",
            kind: 'meta',
          });
        }
      });
      return;
    }
    notificationsEnabled = true;
    localStorage.setItem(NOTIFICATION_PREF_KEY, 'on');
    refreshNotificationsButton();
  }

  function refreshNotificationsButton() {
    const button = document.getElementById('notifications-toggle');
    if (!button) { return; }
    button.textContent = notificationsEnabled ? '🔔' : '🔕';
    button.title = notificationsEnabled
      ? 'Browser notifications: on (click to disable)'
      : 'Browser notifications: off (click to enable)';
  }

  function maybeNotify({ title, body, taskId, kind }) {
    if (!notificationsEnabled) { return; }
    if (typeof Notification === 'undefined' || Notification.permission !== 'granted') {
      return;
    }
    // Skip if the user is already looking at the relevant tab — they
    // don't need a popup for what's right in front of them.
    if (!document.hidden && taskId && taskId === activeTaskId) { return; }
    try {
      const notification = new Notification(title, {
        body: body || '',
        icon: '/logo.png',
        // Same kind+task collapses repeated alerts (e.g. permission
        // re-asks) into one OS-level entry. Different kinds stack.
        tag: `kato-${kind || 'info'}-${taskId || 'global'}`,
      });
      notification.onclick = () => {
        window.focus();
        if (taskId) {
          const tab = tabList.querySelector(
            `.tab[data-task-id="${cssEscapeAttr(taskId)}"]`,
          );
          if (tab) { tab.click(); }
        }
        notification.close();
      };
    } catch (_) {
      // Some browsers throw under stricter policies; degrade silently.
    }
  }

  function cssEscapeAttr(value) {
    if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
      return CSS.escape(String(value));
    }
    return String(value).replace(/(["\\])/g, '\\$1');
  }

  function classifyStatusEntry(entry) {
    const message = (entry && entry.message) || '';

    let match;
    match = message.match(/^task (\S+) tagged kato:wait-planning/);
    if (match) {
      return {
        title: 'Planning chat ready',
        body: match[1],
        taskId: match[1],
        kind: 'started',
      };
    }
    match = message.match(/^Mission (\S+): starting mission(?:: (.+))?/);
    if (match) {
      return {
        title: 'Task started',
        body: match[2] ? `${match[1]}: ${match[2]}` : match[1],
        taskId: match[1],
        kind: 'started',
      };
    }
    match = message.match(/^Mission (\S+): workflow completed successfully/);
    if (match) {
      return {
        title: 'Task completed',
        body: match[1],
        taskId: match[1],
        kind: 'completed',
      };
    }
    match = message.match(
      /^task (\S+): claude is asking permission to run (\S+)/,
    );
    if (match) {
      return {
        title: 'Approval needed',
        body: `${match[1]} → ${match[2]}`,
        taskId: match[1],
        kind: 'attention',
      };
    }
    match = message.match(/^task (\S+): claude turn ended \(error\)/);
    if (match) {
      return {
        title: 'Turn failed',
        body: match[1],
        taskId: match[1],
        kind: 'error',
      };
    }
    return null;
  }

  // ----- live status bar -----

  function initStatusBar() {
    // Constants live inside the function so the early `initStatusBar()`
    // call at the top of this IIFE doesn't hit the TDZ on `const`s
    // declared later in the file.
    const STATUS_HISTORY_LIMIT = 200;
    const STATUS_STALE_AFTER_MS = 30000;

    const bar = document.getElementById('status-bar');
    const text = document.getElementById('status-bar-text');
    const toggle = document.getElementById('status-bar-toggle');
    const history = document.getElementById('status-bar-history');
    if (!bar || !text || !toggle || !history) { return; }

    const seenSequences = new Set();
    let staleTimer = null;

    toggle.addEventListener('click', () => {
      history.hidden = !history.hidden;
      toggle.textContent = history.hidden ? '▾' : '▴';
    });

    function applyEntry(entry) {
      if (!entry || seenSequences.has(entry.sequence)) { return; }
      seenSequences.add(entry.sequence);
      text.textContent = entry.message;
      bar.classList.toggle('is-error', entry.level === 'ERROR');
      bar.classList.toggle('is-warn',
        entry.level === 'WARNING' || entry.level === 'WARN');
      bar.classList.remove('is-stale');
      appendHistoryRow(entry);
      resetStaleTimer();
      const classification = classifyStatusEntry(entry);
      if (classification) {
        maybeNotify(classification);
      }
    }

    function appendHistoryRow(entry) {
      const row = document.createElement('div');
      row.className = 'row' + statusRowClass(entry.level);
      const ts = new Date(entry.epoch * 1000).toLocaleTimeString();
      row.innerHTML =
        `<span class="ts">${escapeHtml(ts)}</span>` +
        `<span class="lvl">${escapeHtml((entry.level || '').slice(0, 4))}</span>` +
        `<span class="msg"></span>`;
      row.querySelector('.msg').textContent = entry.message;
      history.appendChild(row);
      while (history.children.length > STATUS_HISTORY_LIMIT) {
        history.removeChild(history.firstChild);
      }
      history.scrollTop = history.scrollHeight;
    }

    function statusRowClass(level) {
      if (level === 'ERROR') { return ' error'; }
      if (level === 'WARNING' || level === 'WARN') { return ' warn'; }
      return '';
    }

    function resetStaleTimer() {
      if (staleTimer) { clearTimeout(staleTimer); }
      staleTimer = setTimeout(() => bar.classList.add('is-stale'),
        STATUS_STALE_AFTER_MS);
    }

    const stream = new EventSource('/api/status/events');
    stream.addEventListener('status_entry', (event) => {
      const payload = safeParseJSON(event.data);
      if (payload) { applyEntry(payload); }
    });
    stream.addEventListener('status_disabled', () => {
      text.textContent = 'Live status feed is unavailable';
      bar.classList.add('is-stale');
      stream.close();
    });
    stream.onerror = () => {
      if (stream.readyState === EventSource.CLOSED) {
        bar.classList.add('is-stale');
      }
    };
    resetStaleTimer();
  }

  function insertIntoMessageInput(text) {
    const start = messageInput.selectionStart ?? messageInput.value.length;
    const end = messageInput.selectionEnd ?? messageInput.value.length;
    const before = messageInput.value.slice(0, start);
    const after = messageInput.value.slice(end);
    const needsLeadingSpace = before && !/\s$/.test(before);
    const fragment = (needsLeadingSpace ? ' ' : '') + text;
    messageInput.value = before + fragment + after;
    const cursor = before.length + fragment.length;
    messageInput.focus();
    messageInput.setSelectionRange(cursor, cursor);
  }

  function setTurnInFlight(busy) {
    activeTurnInFlight = !!busy;
    refreshSendButton();
  }

  function applyBranchState(state) {
    activeBranchState = {
      expected: String(state && state.expected || ''),
      current: String(state && state.current || ''),
      locked: !!(state && state.locked),
    };
    refreshSendButton();
  }

  function refreshSendButton() {
    if (activeBranchState.locked) {
      sendButton.disabled = true;
      sendButton.textContent = 'Locked';
      sendButton.title =
        `Repo is on '${activeBranchState.current}' but this session expects ` +
        `'${activeBranchState.expected}'. ` +
        `Kato has switched to another task. Wait for the repo to return to ` +
        `'${activeBranchState.expected}' before chatting.`;
      return;
    }
    // Always enabled when not branch-locked. Claude's stream-json input
    // accepts steering messages mid-turn — the user can interject as
    // soon as they have something to say.
    sendButton.disabled = false;
    sendButton.textContent = activeTurnInFlight ? 'Steer' : 'Send';
    sendButton.title = activeTurnInFlight
      ? 'Claude is working — your message will steer the in-flight turn.'
      : '';
  }

  function lockSendDisabled(reason) {
    activeTurnInFlight = false;
    sendButton.disabled = true;
    sendButton.textContent = 'Send';
    sendButton.title = reason || 'Cannot send';
  }

  function showPlaceholder() {
    detail.hidden = true;
    placeholder.style.display = '';
    activeTaskId = null;
    notifyActiveTaskChanged('');
  }

  function showDetail(taskId, tabElement) {
    placeholder.style.display = 'none';
    detail.hidden = false;
    headerTaskId.textContent = taskId;
    const summary = tabElement.querySelector('p')?.textContent || '';
    headerTaskSummary.textContent = summary;
    const status = (tabElement.querySelector('.status-dot')?.title) || 'active';
    headerStatusDot.className = `status-dot status-${status}`;
    headerStatusDot.title = status;
    messageInput.value = '';
    messageInput.focus();
  }

  // ----- SSE stream -----

  function openStream(taskId) {
    const url = `/api/sessions/${encodeURIComponent(taskId)}/events`;
    const stream = new EventSource(url);
    activeStream = stream;

    stream.addEventListener('session_event', (event) => {
      const payload = safeParseJSON(event.data);
      if (payload) { renderEvent(payload.event || payload); }
    });

    stream.addEventListener('branch_state', (event) => {
      const payload = safeParseJSON(event.data) || {};
      applyBranchState(payload);
    });

    stream.addEventListener('session_idle', (event) => {
      const payload = safeParseJSON(event.data) || {};
      const status = payload.status || 'terminated';
      appendBubble('system',
        `(no live subprocess for this tab — last status: ${status})`);
      lockSendDisabled('No live session to chat with.');
      stream.close();
      if (activeStream === stream) { activeStream = null; }
    });

    stream.addEventListener('session_missing', () => {
      appendBubble('error', 'No record for this task on the server.');
      lockSendDisabled('No live session to chat with.');
      stream.close();
      if (activeStream === stream) { activeStream = null; }
    });

    stream.addEventListener('session_closed', () => {
      appendBubble('system', '(session ended)');
      lockSendDisabled('Session has ended.');
      stream.close();
      if (activeStream === stream) { activeStream = null; }
    });

    stream.onerror = () => {
      // EventSource auto-reconnects on transient drops; only treat it as
      // fatal if we explicitly closed it.
      if (stream.readyState === EventSource.CLOSED) {
        appendBubble('error', 'Stream closed unexpectedly.');
        lockSendDisabled('Stream closed.');
      }
    };
  }

  function closeActiveStream() {
    if (activeStream) {
      try { activeStream.close(); } catch (_) { /* ignore */ }
      activeStream = null;
    }
  }

  // ----- send message / permission -----

  async function onSendMessage(event) {
    event.preventDefault();
    if (activeBranchState.locked) {
      appendBubble('error',
        `Cannot send — repo is on '${activeBranchState.current}' ` +
        `but this session expects '${activeBranchState.expected}'.`);
      return;
    }
    // Mid-turn sends are intentional steering, not a guard violation.
    const text = messageInput.value.trim();
    if (!text || !activeTaskId) { return; }
    appendBubble('user', text);
    messageInput.value = '';
    setTurnInFlight(true);
    try {
      const response = await fetch(
        `/api/sessions/${encodeURIComponent(activeTaskId)}/messages`,
        {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ text }),
        },
      );
      if (!response.ok) {
        const detail = await safeReadError(response);
        appendBubble('error', `send failed: ${detail}`);
        // Send didn't land — let the user retry immediately.
        setTurnInFlight(false);
        return;
      }
      appendBubble('system', '✓ delivered');
    } catch (err) {
      appendBubble('error', `send failed: ${err}`);
      setTurnInFlight(false);
    }
  }

  async function stopActiveSession() {
    if (!activeTaskId) { return; }
    if (stopButton) {
      stopButton.disabled = true;
      stopButton.textContent = 'Stopping…';
    }
    try {
      const response = await fetch(
        `/api/sessions/${encodeURIComponent(activeTaskId)}/stop`,
        { method: 'POST' },
      );
      if (!response.ok) {
        const detail = await safeReadError(response);
        appendBubble('error', `stop failed: ${detail}`);
      } else {
        appendBubble('system', '✗ session stopped');
      }
    } catch (err) {
      appendBubble('error', `stop failed: ${err}`);
    } finally {
      if (stopButton) {
        stopButton.disabled = false;
        stopButton.textContent = 'Stop';
      }
    }
  }

  async function respondToPermission(allow, options) {
    const silent = !!(options && options.silent);
    if (!pendingPermission || !activeTaskId) {
      hidePermissionModal();
      return;
    }
    const requestId = permissionModal.dataset.requestId
      || pendingPermission.request_id
      || pendingPermission.id
      || '';
    if (!requestId) {
      appendBubble('error', 'permission request had no id; cannot respond');
      hidePermissionModal();
      return;
    }
    // Capture the "remember for this session" flag BEFORE we hide the
    // modal — otherwise the checkbox state is gone by the time we'd
    // read it. The auto-allow code path passes silent=true and never
    // shows the modal, so it bypasses this entirely.
    const remember = !silent && permissionRemember && permissionRemember.checked;
    const toolName = String(permissionModal.dataset.toolName || '');
    if (remember && toolName) {
      sessionToolDecisions[toolName] = allow ? 'allow' : 'deny';
    }
    try {
      const response = await fetch(
        `/api/sessions/${encodeURIComponent(activeTaskId)}/permission`,
        {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            request_id: requestId,
            allow,
            rationale: permissionRationale ? permissionRationale.value : '',
          }),
        },
      );
      if (!response.ok) {
        const detail = await safeReadError(response);
        appendBubble('error', `permission send failed: ${detail}`);
      } else if (!silent) {
        appendBubble('system',
          `${allow ? '✓ approved' : '✗ denied'} permission ${requestId}` +
          (remember && toolName ? ` (remembered for ${toolName})` : ''));
      }
    } catch (err) {
      appendBubble('error', `permission send failed: ${err}`);
    }
    hidePermissionModal();
  }

  // ----- event renderer -----

  function renderEvent(envelope) {
    const raw = (envelope && envelope.raw) || envelope;
    if (!raw || !raw.type) { return; }

    switch (raw.type) {
      case 'system':
        if (raw.subtype === 'init') {
          appendBubble('system', `session_id: ${raw.session_id || '(none yet)'}`);
        }
        return;
      case 'assistant':
        renderAssistantEvent(raw);
        return;
      case 'user':
        return;
      case 'result':
        renderResultEvent(raw);
        return;
      case 'permission_request':
      case 'control_request':
        // ``control_request`` is what `--permission-prompt-tool stdio`
        // emits when Claude wants to use a tool. Older builds also send
        // ``permission_request`` directly; both feed the same modal.
        showPermissionModal(raw);
        // Wake the user even if they tabbed away. This is the primary
        // "needs your input" trigger — without an answer Claude just
        // sits idle waiting for stdin.
        maybeNotify({
          title: 'Approval needed',
          body: extractToolNameFromRaw(raw),
          taskId: activeTaskId,
          kind: 'attention',
        });
        return;
      case 'stream_event':
        return;
      default:
        appendBubble('tool',
          `${raw.type}${raw.subtype ? ' / ' + raw.subtype : ''}`);
    }
  }

  function renderAssistantEvent(raw) {
    const message = raw.message || {};
    const content = Array.isArray(message.content) ? message.content : [];
    const textPieces = [];
    for (const block of content) {
      if (!block || typeof block !== 'object') { continue; }
      if (block.type === 'text' && block.text) {
        textPieces.push(block.text);
      } else if (block.type === 'tool_use') {
        const toolName = block.name || 'tool';
        const inputSummary = stringifyShort(block.input);
        appendBubble('tool', `→ ${toolName}(${inputSummary})`);
      }
    }
    if (textPieces.length > 0) {
      appendBubble('assistant', textPieces.join('\n'));
    }
    // An assistant event means a turn is in progress. The matching
    // `result` event will clear this. Reconnects mid-turn pick this up
    // from the backlog replay so the button locks correctly.
    setTurnInFlight(true);
  }

  function renderResultEvent(raw) {
    const ok = !raw.is_error;
    const summary = raw.result || (ok ? 'completed' : 'failed');
    appendBubble(ok ? 'system' : 'error',
      `(result: ${ok ? 'success' : 'error'}) ${summary}`);
    // Turn ended — the next user message can be sent.
    setTurnInFlight(false);
    // Errors always notify; success only if the user has tabbed away
    // (the visible-tab guard inside maybeNotify handles foreground).
    maybeNotify({
      title: ok ? 'Claude replied' : 'Turn failed',
      body: typeof summary === 'string' ? summary.slice(0, 140) : '',
      taskId: activeTaskId,
      kind: ok ? 'reply' : 'error',
    });
  }

  function unpackPermissionEnvelope(raw) {
    // ``control_request`` (the modern shape from ``--permission-prompt-tool
    // stdio``) nests payload under ``request``; older ``permission_request``
    // puts the same fields at the top level. Read either.
    const nested = (raw && typeof raw.request === 'object' && raw.request) || {};
    return {
      requestId: String(raw.request_id || raw.id || ''),
      toolName: String(
        raw.tool_name || raw.tool || nested.tool_name || nested.tool || 'tool',
      ),
      toolInput: raw.input || nested.input || {},
    };
  }

  function extractToolNameFromRaw(raw) {
    return unpackPermissionEnvelope(raw).toolName;
  }

  // ----- permission modal -----

  function showPermissionModal(raw) {
    pendingPermission = raw;
    const { requestId, toolName, toolInput } = unpackPermissionEnvelope(raw);

    // If user previously checked "don't ask again this session" for
    // this tool, skip the modal entirely and respond with the saved
    // decision. The audit bubble in the chat log shows what happened.
    const remembered = sessionToolDecisions[toolName];
    if (remembered) {
      permissionModal.dataset.requestId = requestId;
      pendingPermission = raw;
      respondToPermission(remembered === 'allow', { silent: true });
      appendBubble('system',
        `(auto-${remembered}ed for ${toolName} — remembered for this session)`);
      return;
    }

    permissionToolName.textContent = toolName;
    permissionRememberTool.textContent = toolName;
    permissionRemember.checked = false;
    renderPermissionFields(toolInput);
    permissionDetail.textContent = (() => {
      try { return JSON.stringify(raw, null, 2); }
      catch (_) { return String(raw); }
    })();
    permissionRationale.value = '';
    permissionModal.hidden = false;
    permissionModal.dataset.requestId = requestId;
    permissionModal.dataset.toolName = toolName;
  }

  function renderPermissionFields(toolInput) {
    permissionFields.innerHTML = '';
    if (!toolInput || typeof toolInput !== 'object'
        || Object.keys(toolInput).length === 0) {
      const empty = document.createElement('p');
      empty.className = 'permission-field-value';
      empty.textContent = '(no arguments)';
      permissionFields.appendChild(empty);
      return;
    }
    for (const [key, value] of Object.entries(toolInput)) {
      const field = document.createElement('div');
      field.className = 'permission-field';
      const label = document.createElement('span');
      label.className = 'permission-field-label';
      label.textContent = key;
      const body = document.createElement('div');
      body.className = 'permission-field-value';
      body.textContent = formatPermissionFieldValue(value);
      field.appendChild(label);
      field.appendChild(body);
      permissionFields.appendChild(field);
    }
  }

  function formatPermissionFieldValue(value) {
    if (value == null) { return ''; }
    if (typeof value === 'string') { return value; }
    if (typeof value === 'number' || typeof value === 'boolean') {
      return String(value);
    }
    try { return JSON.stringify(value, null, 2); }
    catch (_) { return String(value); }
  }

  function hidePermissionModal() {
    permissionModal.hidden = true;
    pendingPermission = null;
    permissionModal.dataset.requestId = '';
  }

  // ----- helpers -----

  function appendBubble(kind, text) {
    const div = document.createElement('div');
    div.className = `bubble ${kind}`;
    div.textContent = text;
    eventLog.appendChild(div);
    eventLog.scrollTop = eventLog.scrollHeight;
  }

  function safeParseJSON(text) {
    try { return JSON.parse(text); } catch (_) { return null; }
  }

  async function safeReadError(response) {
    try {
      const body = await response.json();
      return body.error || JSON.stringify(body);
    } catch (_) {
      return `${response.status} ${response.statusText}`;
    }
  }

  function stringifyShort(obj) {
    try {
      const text = JSON.stringify(obj);
      return text && text.length > 120 ? text.slice(0, 117) + '…' : text || '';
    } catch (_) {
      return '';
    }
  }

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function escapeAttr(text) {
    return escapeHtml(text).replace(/"/g, '&quot;');
  }
})();
