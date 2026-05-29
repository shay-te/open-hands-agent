// Shared modal scrim + chrome: a backdrop that closes on outside-click,
// the centered dialog box, and a header with a title and a × close
// button. ``extraClass`` adds a per-modal class to the dialog box (e.g.
// 'adopt-task-modal', 'commit-diff-modal'); ``title`` may be a node
// (CommitDiffModal passes a <code> + subject). ``children`` is the body
// (and footer, if the modal has one).
export default function ModalShell({ ariaLabel, title, extraClass, onClose, children }) {
  return (
    <div
      className="adopt-session-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel}
      onClick={(e) => {
        if (e.target === e.currentTarget) { onClose(); }
      }}
    >
      <div className={extraClass ? `adopt-session-modal ${extraClass}` : 'adopt-session-modal'}>
        <header className="adopt-session-modal-header">
          <h2>{title}</h2>
          <button
            type="button"
            className="adopt-session-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
