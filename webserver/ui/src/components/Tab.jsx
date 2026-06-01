import { useRef, useState } from 'react';
import { cx } from '../utils/cx.js';
import { deriveTabStatus, tabStatusTitle } from '../utils/tabStatus.js';
import { deriveAgentStatus, badgeKindFor } from '../utils/agentStatus.js';
import Icon from './Icon.jsx';
import TabTooltip from './TabTooltip.jsx';

// Delay before the hover card appears — long enough that scrubbing
// across the strip to reach a far tab doesn't flash a card on every
// tab it passes over.
const HOVER_DELAY_MS = 350;

export default function Tab({
  session, active, needsAttention, liveStatus = null, pinned = false,
  onSelect, onForget, onTogglePin,
}) {
  const baseStatus = deriveTabStatus(session);
  // The agent dot + tooltip badge derive from the SAME value as the header chip
  // (UNA-2492). For the active tab the live SSE status (from agentStatusStore)
  // wins; background tabs fall back to the polled session fields.
  const agent = deriveAgentStatus(session, active ? liveStatus : null, needsAttention);
  const className = cx(
    'tab',
    active && 'active',
    needsAttention && 'needs-attention',
    pinned && 'is-pinned',
  );
  const dotClass = agent.dotClass;

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
  function handleTogglePin(event) {
    event.stopPropagation();
    closeTooltip();
    if (typeof onTogglePin !== 'function') { return; }
    onTogglePin(session.task_id);
  }

  const hasChangesPending = !!session.has_changes_pending;
  const changesIndicator = hasChangesPending && (
    <span className="tab-changes-indicator" aria-hidden="true">
      <Icon name="commit" />
    </span>
  );

  const model = buildTooltipModel(session, baseStatus, needsAttention, agent);

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
          className={cx('tab-pin-btn', pinned && 'is-pinned')}
          aria-label={pinned ? 'Unpin this task' : 'Pin this task'}
          aria-pressed={pinned}
          title={pinned ? 'Unpin tab' : 'Pin tab to the left'}
          onClick={handleTogglePin}
        >
          <Icon name="pin" />
        </button>
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
function buildTooltipModel(session, baseStatus, needsAttention, agent) {
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
    statusKey: agent.status,
    claudeBadge: agentTooltipBadge(agent),
    rows,
  };
}


// Compact Claude liveness badge for the hover card — now derived from the
// shared agent status (utils/agentStatus.js) so the card, the tab dot, and the
// chat header all speak the same language (UNA-2492). Returns null for kinds
// with no badge styling (provisioning/missing/unknown), matching the old
// claudeBadge's null.
function agentTooltipBadge(agent) {
  const kind = badgeKindFor(agent.kind);
  if (!kind) { return null; }
  return { kind, label: `Claude: ${agent.label}` };
}
