// Shared chrome for the two ``.modal`` dialogs (PermissionModal and
// ForgetTaskModal). Renders the
//
//   div.modal > div.modal-card > header.modal-head(h2 + subtitle) + {children}
//
// shape. Backdrop dismissal is opt-in because the two callers differ:
// ForgetTaskModal closes on a backdrop click; PermissionModal has no
// backdrop dismiss (the operator must make an explicit decision). Esc
// handling stays with the caller (ForgetTaskModal uses ``useEscapeKey``)
// since it depends on the caller's own mount lifecycle. The id
// attributes are passed through so existing selectors/tests keep
// working (#permission-modal-title, #forget-task-title, etc.).
export default function DialogShell({
  id,
  title,
  subtitle,
  subtitleId,
  ariaLabelledBy,
  onClose,
  backdropClose = false,
  children,
}) {
  function handleBackdropClick(event) {
    if (event.target === event.currentTarget) {
      onClose();
    }
  }

  return (
    <div
      id={id}
      className="modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby={ariaLabelledBy}
      onClick={backdropClose ? handleBackdropClick : undefined}
    >
      <div className="modal-card">
        <header className="modal-head">
          <h2 id={ariaLabelledBy}>{title}</h2>
          <span id={subtitleId}>{subtitle}</span>
        </header>
        {children}
      </div>
    </div>
  );
}
