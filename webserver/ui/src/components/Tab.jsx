import { useRef, useState } from 'react';
import { TAB_STATUS } from '../constants/tabStatus.js';
import { deriveTabStatus, resolveTabStatus, tabStatusTitle } from '../utils/tabStatus.js';
import Icon from './Icon.jsx';
import TabTooltip from './TabTooltip.jsx';

// Delay before the hover card appears — long enough that scrubbing
// across the strip to reach a far tab doesn't flash a card on every
// tab it passes over.
const HOVER_DELAY_MS = 350;

export default function Tab({ session, active, needsAttention, onSelect, onForget }) {
  const baseStatus = deriveTabStatus(session);
  const status = resolveTabStatus(session, needsAttention);
  const isLoading = baseStatus === TAB_STATUS.PROVISIONING;
  const className = [
    'tab',
    active ? 'active' : '',
    needsAttention ? 'needs-attention' : '',
  ].filter(Boolean).join(' ');
  const idleAlive = status === TAB_STATUS.ACTIVE && session?.working === false;
  const dotClass = [
    'status-dot',
    `status-${status}`,
    isLoading ? 'is-loading' : '',
    idleAlive ? 'is-idle-alive' : '',
  ].filter(Boolean).join(' ');

  // Hover-card state. ``anchorRect`` is a frozen snapshot of the
  // <li>'s viewport rect taken when the card opens — TabTooltip
  // positions itself off it (and re-measures its own height).
  const liRef = useRef(null);
  const timerRef = useRef(null);
  const [anchorRect, setAnchorRect] = useState(null);

  function openTooltipSoon() {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      if (liRef.current) {
        setAnchorRect(liRef.current.getBoundingClientRect());
      }
    }, HOVER_DELAY_MS);
  }
  function closeTooltip() {
    clearTimeout(timerRef.current);
    setAnchorRect(null);
  }

  function handleSelect() {
    closeTooltip();
    onSelect(session.task_id);
  }
  function handleForget(event) {
    event.stopPropagation();
    closeTooltip();
    if (typeof onForget !== 'function') { return; }
    // Don't act here — hand off to App, which opens the
    // ForgetTaskModal hard-confirm. Forgetting is destructive, so
    // the operator must explicitly approve it in that dialog.
    onForget(session.task_id);
  }

  const hasChangesPending = !!session.has_changes_pending;
  const changesIndicator = hasChangesPending && (
    <span className="tab-changes-indicator" aria-hidden="true">
      <Icon name="commit" />
    </span>
  );

  const model = buildTooltipModel(session, baseStatus, needsAttention, status);

  return (
    <>
      <li
        ref={liRef}
        className={className}
        data-task-id={session.task_id}
        onClick={handleSelect}
        onMouseEnter={openTooltipSoon}
        onMouseLeave={closeTooltip}
        // Keyboard parity: focusing the tab (tab-key nav) also
        // surfaces the card.
        onFocus={openTooltipSoon}
        onBlur={closeTooltip}
        tabIndex={0}
      >
        <span className={dotClass} />
        <strong>{session.task_id}</strong>
        {changesIndicator}
        <button
          type="button"
          className="tab-forget-btn"
          aria-label="Forget this task"
          onClick={handleForget}
        >
          <Icon name="xmark" />
        </button>
      </li>
      {anchorRect && (
        <TabTooltip anchorRect={anchorRect} model={model} />
      )}
    </>
  );
}


// Structured tooltip model — every fact the old ` · `-joined string
// carried, now as discrete fields the card renders as a header +
// labelled rows.
function buildTooltipModel(session, baseStatus, needsAttention, statusKey) {
  const taskId = String(session?.task_id || '').trim() || 'Task';
  const summary = String(session?.task_summary || '').trim();
  const rows = [];

  const statusLine = tabStatusTitle(baseStatus, needsAttention);
  if (statusLine) {
    rows.push({ label: 'Status', value: statusLine });
  }

  const branch = String(session?.branch_name || '').trim();
  if (branch) { rows.push({ label: 'Branch', value: branch }); }

  const repoIds = Array.isArray(session?.repository_ids)
    ? session.repository_ids.filter(Boolean)
    : [];
  if (repoIds.length === 1) {
    rows.push({ label: 'Repo', value: repoIds[0] });
  } else if (repoIds.length > 1) {
    rows.push({
      label: `Repos (${repoIds.length})`,
      value: repoIds.join(', '),
    });
  }

  if (session?.has_pending_permission) {
    const tool = String(session?.pending_permission_tool_name || '').trim();
    rows.push({
      label: 'Permission',
      value: tool ? `Awaiting decision for ${tool}` : 'Awaiting your decision',
      tone: 'warn',
    });
  }
  if (session?.has_changes_pending) {
    rows.push({
      label: 'Changes',
      value: 'Ready to push — waiting for your approval',
      tone: 'warn',
    });
  }
  const pushedPr = String(
    session?.pr_url || session?.pull_request_url || '',
  ).trim();
  if (pushedPr) { rows.push({ label: 'PR', value: pushedPr }); }

  return {
    taskId,
    summary,
    statusKey,
    claudeBadge: claudeBadge(session),
    rows,
  };
}


// Compact Claude liveness badge for the card header. Mirrors the
// chip wording SessionHeader uses so the tab card and the chat
// header speak the same language.
function claudeBadge(session) {
  if (!session) { return null; }
  if (session.live === false) {
    return { kind: 'sleep', label: 'Claude: sleeping' };
  }
  if (session.working === true) {
    return { kind: 'work', label: 'Claude: working' };
  }
  if (session.has_pending_permission) {
    return { kind: 'wait', label: 'Claude: paused' };
  }
  if (session.live === true) {
    return { kind: 'idle', label: 'Claude: idle' };
  }
  return null;
}
