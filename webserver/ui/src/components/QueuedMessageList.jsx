import Icon from './Icon.jsx';
import { countNoun } from '../utils/pluralize.js';

// Floating list of queued chat messages, rendered above the
// MessageForm composer. Each row shows the queued text (truncated)
// + a Steer button that delivers the message NOW (mid-turn) + a
// trash button that drops the item entirely. Empty list renders
// nothing so the composer hugs the bottom as before.
//
// The list lives between EventLog and MessageForm in the DOM so
// pinned-bottom layout (the floating glass composer capsule) is
// untouched. On busy turns the operator can see at a glance what's
// stacked up waiting, edit their mental ordering (delete + retype),
// or promote a follow-up via Steer.
//
// Each item shape: ``{ id, text, images?, queuedAt }``.

export default function QueuedMessageList({
  items,
  onSteer,
  onRemove,
}) {
  if (!Array.isArray(items) || items.length === 0) {
    return null;
  }
  return (
    <ul className="queued-message-list" aria-label="Queued messages">
      {items.map((item) => (
        <QueuedRow
          key={item.id}
          item={item}
          onSteer={onSteer}
          onRemove={onRemove}
        />
      ))}
    </ul>
  );
}

function QueuedRow({ item, onSteer, onRemove }) {
  const text = String(item?.text || '').trim();
  const imageCount = Array.isArray(item?.images) ? item.images.length : 0;
  const display = imageCount > 0
    ? `${text}${text ? ' · ' : ''}(${countNoun(imageCount, 'image')})`
    : text;
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
