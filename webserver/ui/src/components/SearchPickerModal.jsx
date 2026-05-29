import { useState } from 'react';
import { cx } from '../utils/cx.js';
import ModalShell from './ModalShell.jsx';
import ModalFooterActions from './ModalFooterActions.jsx';

// Shared search-picker modal: ModalShell scrim/header + a search input +
// a selectable list + a Cancel/Confirm footer. The three picker modals
// (adopt session / add repository / adopt task) differ only in how they
// load + filter their items, how a row renders, and what confirm does —
// all injected as props. This component owns the generic picker state:
// the highlighted row and the in-flight confirm guard.
//
// The CALLER owns ``query`` (passed in with ``onQueryChange``) so it can
// drive either a server-side search (feed query into its fetch) or a
// client-side filter, and passes the already-computed ``items`` to render.
// ``onConfirm(selectedItem)`` does the caller's API call + toast + close;
// busy/disabled handling is managed here.
export default function SearchPickerModal({
  ariaLabel,
  title,
  extraClass,
  onClose,
  helpText,
  searchPlaceholder,
  query,
  onQueryChange,
  items,
  loading,
  error,
  loadingText,
  emptyText,
  getItemId,
  renderRow,
  rowClassName,
  onConfirm,
  confirmLabel,
  busyLabel,
}) {
  const [selectedId, setSelectedId] = useState('');
  const [busy, setBusy] = useState(false);
  const selectedItem = items.find((item) => getItemId(item) === selectedId);

  async function confirm() {
    if (!selectedItem || busy) { return; }
    setBusy(true);
    try {
      await onConfirm(selectedItem);
    } finally {
      setBusy(false);
    }
  }

  return (
    <ModalShell
      ariaLabel={ariaLabel}
      title={title}
      extraClass={extraClass}
      onClose={onClose}
    >
      <p className="adopt-session-modal-help">{helpText}</p>
      <input
        type="text"
        className="adopt-session-search"
        placeholder={searchPlaceholder}
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        autoFocus
      />
      <div className="adopt-session-list">
        {loading && (
          <div className="adopt-session-empty">{loadingText}</div>
        )}
        {!loading && error && (
          <div className="adopt-session-empty adopt-session-error">{error}</div>
        )}
        {!loading && !error && items.length === 0 && (
          <div className="adopt-session-empty">{emptyText}</div>
        )}
        {!loading && !error && items.map((item) => {
          const id = getItemId(item);
          return (
            <button
              type="button"
              key={id}
              className={cx(
                'adopt-session-row',
                id === selectedId && 'is-selected',
                rowClassName && rowClassName(item),
              )}
              onClick={() => setSelectedId(id)}
            >
              {renderRow(item)}
            </button>
          );
        })}
      </div>
      <ModalFooterActions
        onCancel={onClose}
        onConfirm={confirm}
        busy={busy}
        canConfirm={!!selectedItem}
        confirmLabel={confirmLabel}
        busyLabel={busyLabel}
      />
    </ModalShell>
  );
}
