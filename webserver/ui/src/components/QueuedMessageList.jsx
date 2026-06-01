import { useRef, useState } from 'react';
import Icon from './Icon.jsx';
import { withImageCountSuffix } from '../utils/pluralize.js';
import { usePublishedHeight } from '../hooks/usePublishedHeight.js';

// Floating list of queued chat messages, rendered above the
// MessageForm composer. Each row shows the queued text (truncated)
// + an Edit button (revise the text in place) + a Steer button that
// delivers the message NOW (mid-turn) + a trash button that drops the
// item entirely. Empty list renders nothing so the composer hugs the
// bottom as before.
//
// The list lives between EventLog and MessageForm in the DOM so
// pinned-bottom layout (the floating glass composer capsule) is
// untouched. On busy turns the operator can see at a glance what's
// stacked up waiting, revise a queued item in place via Edit, or
// promote a follow-up via Steer.
//
// Each item shape: ``{ id, text, images?, queuedAt }``.

export default function QueuedMessageList({
  items,
  onSteer,
  onRemove,
  onEdit,
}) {
  const listRef = useRef(null);
  const hasItems = Array.isArray(items) && items.length > 0;
  // Reserve room for this floating list at the bottom of #event-log so
  // it never covers the working indicator (the log's last entry).
  usePublishedHeight('--queued-h', listRef, hasItems);
  if (!hasItems) {
    return null;
  }
  return (
    <ul ref={listRef} className="queued-message-list" aria-label="Queued messages">
      {items.map((item) => (
        <QueuedRow
          key={item.id}
          item={item}
          onSteer={onSteer}
          onRemove={onRemove}
          onEdit={onEdit}
        />
      ))}
    </ul>
  );
}

function QueuedRow({ item, onSteer, onRemove, onEdit }) {
  const text = String(item?.text || '').trim();
  const imageCount = Array.isArray(item?.images) ? item.images.length : 0;
  const display = withImageCountSuffix(text, imageCount, { separator: ' · ', label: '' });

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text);

  function startEdit() {
    setDraft(String(item?.text || ''));
    setEditing(true);
  }
  function cancelEdit() {
    setDraft(String(item?.text || ''));
    setEditing(false);
  }
  function saveEdit() {
    const next = draft.trim();
    // An emptied edit would leave a useless blank steer — treat it as a
    // cancel rather than saving an empty message.
    if (!next) { cancelEdit(); return; }
    if (onEdit) { onEdit(item.id, next); }
    setEditing(false);
  }
  function onKeyDown(event) {
    if (event.key === 'Escape') {
      event.preventDefault();
      cancelEdit();
    } else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      // Cmd/Ctrl+Enter saves; plain Enter inserts a newline (queued
      // messages can be multi-line), matching the composer's convention.
      event.preventDefault();
      saveEdit();
    }
  }

  if (editing) {
    return (
      <li className="queued-message-row is-editing">
        <textarea
          className="queued-message-edit-input"
          aria-label="Edit queued message"
          value={draft}
          autoFocus
          rows={1}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          className="queued-message-save tooltip-above"
          data-tooltip="Save changes to this queued message (⌘/Ctrl+Enter)."
          aria-label="Save edit"
          onClick={saveEdit}
          disabled={!draft.trim()}
        >
          <Icon name="check" />
        </button>
        <button
          type="button"
          className="queued-message-cancel tooltip-above"
          data-tooltip="Discard changes (Esc)."
          aria-label="Cancel edit"
          onClick={cancelEdit}
        >
          <Icon name="xmark" />
        </button>
      </li>
    );
  }

  return (
    <li className="queued-message-row">
      <span className="queued-message-glyph" aria-hidden="true">
        <Icon name="history" />
      </span>
      <span
        className="queued-message-text"
        title={text}
      >
        {display || '(empty draft)'}
      </span>
      <button
        type="button"
        className="queued-message-edit tooltip-above"
        data-tooltip="Edit this queued message before it is sent."
        aria-label="Edit queued message"
        onClick={startEdit}
      >
        <Icon name="edit" />
      </button>
      <button
        type="button"
        className="queued-message-steer tooltip-above"
        data-tooltip="Steer — deliver this message NOW, even though Claude is mid-turn. Use to course-correct without waiting for the current turn to finish."
        aria-label="Steer (deliver now)"
        onClick={() => onSteer && onSteer(item.id)}
      >
        <Icon name="arrow-up" />
        <span className="queued-message-steer-label">Steer</span>
      </button>
      <button
        type="button"
        className="queued-message-remove tooltip-above"
        data-tooltip="Remove this queued message — it will not be sent."
        aria-label="Remove queued message"
        onClick={() => onRemove && onRemove(item.id)}
      >
        <Icon name="xmark" />
      </button>
    </li>
  );
}
