// The Cancel / Confirm footer shared by the picker modals. Cancel is
// disabled while ``busy``; Confirm is disabled until ``canConfirm`` (a
// selection exists) and while busy, and shows ``busyLabel`` mid-flight.
export default function ModalFooterActions({
  onCancel,
  onConfirm,
  busy = false,
  canConfirm = false,
  cancelLabel = 'Cancel',
  confirmLabel = 'Confirm',
  busyLabel,
}) {
  return (
    <footer className="adopt-session-modal-footer">
      <button
        type="button"
        className="adopt-session-cancel"
        onClick={onCancel}
        disabled={busy}
      >
        {cancelLabel}
      </button>
      <button
        type="button"
        className="adopt-session-confirm"
        onClick={onConfirm}
        disabled={!canConfirm || busy}
      >
        {busy && busyLabel ? busyLabel : confirmLabel}
      </button>
    </footer>
  );
}
