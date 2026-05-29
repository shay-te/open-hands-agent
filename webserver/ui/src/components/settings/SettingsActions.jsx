// The two-button footer shared by the settings panels: a secondary
// action (Revert / Refresh) disabled while saving, and a primary Save
// disabled until there are unsaved changes (and while saving). Labels
// are props so each panel keeps its own wording (e.g. Schema's dynamic
// "Save N changes"); ``primaryLabel`` may be a string or a node.
export default function SettingsActions({
  onSecondary,
  secondaryLabel = 'Revert',
  secondaryDisabled,
  onSave,
  saving = false,
  canSave = false,
  primaryLabel = 'Save',
  savingLabel = 'Saving…',
}) {
  return (
    <div className="settings-drawer-actions">
      <button
        type="button"
        className="settings-drawer-action-secondary"
        onClick={onSecondary}
        disabled={secondaryDisabled === undefined ? saving : secondaryDisabled}
      >
        {secondaryLabel}
      </button>
      <button
        type="button"
        className="settings-drawer-action-primary"
        onClick={onSave}
        disabled={!canSave || saving}
      >
        {saving ? savingLabel : primaryLabel}
      </button>
    </div>
  );
}
