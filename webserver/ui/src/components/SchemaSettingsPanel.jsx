import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { fetchAllSettings, updateAllSettings } from '../api.js';
import { toast } from '../stores/toastStore.js';

// Generic, schema-driven settings panel. One instance renders ONE
// section of the ``/api/all-settings`` schema (General, Claude
// agent, Sandbox, Security scanner, Email & Slack, OpenHands,
// Docker/infra, AWS). Field widgets are chosen from ``field.type``;
// ``warning`` / ``danger`` annotations render inline. The section's
// own ``warning`` renders as a banner (the Sandbox tab uses this).
//
// Writes go to ~/.kato/settings.json via POST /api/all-settings
// (server whitelists to the schema). The operator's .env is never
// touched. Restart required — banner shown after a save.

function sourceLabel(source) {
  if (source === 'env') { return 'live'; }
  if (source === 'kato_settings') { return 'saved'; }
  if (source === 'env_file') { return '.env'; }
  return 'unset';
}

export default function SchemaSettingsPanel({ sectionId }) {
  const [state, setState] = useState({
    loading: true,
    error: '',
    sections: [],
    settingsFilePath: '',
  });
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(null);

  const refresh = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: '' }));
    const result = await fetchAllSettings();
    if (!result.ok) {
      setState({
        loading: false,
        error: String(result.body?.error || result.error || 'load failed'),
        sections: [], settingsFilePath: '',
      });
      return;
    }
    const body = result.body || {};
    const sections = Array.isArray(body.sections) ? body.sections : [];
    setState({
      loading: false,
      error: '',
      sections,
      settingsFilePath: String(body.settings_file_path || ''),
    });
    // Seed the draft from server values for THIS section's fields.
    const section = sections.find((s) => s.id === sectionId);
    const seed = {};
    for (const f of (section?.fields || [])) {
      seed[f.key] = f.value ?? '';
    }
    setDraft(seed);
  }, [sectionId]);

  useEffect(() => { refresh(); }, [refresh]);

  const section = useMemo(
    () => state.sections.find((s) => s.id === sectionId) || null,
    [state.sections, sectionId],
  );

  const dirtyKeys = useMemo(() => {
    if (!section) { return []; }
    const out = [];
    for (const f of section.fields) {
      const server = f.value ?? '';
      const current = draft[f.key] ?? '';
      if (String(current) !== String(server)) { out.push(f.key); }
    }
    return out;
  }, [section, draft]);

  function setField(key, value) {
    setDraft((cur) => ({ ...cur, [key]: value }));
  }

  async function save() {
    if (dirtyKeys.length === 0) { return; }
    const updates = {};
    for (const k of dirtyKeys) { updates[k] = draft[k]; }
    setSaving(true);
    try {
      const result = await updateAllSettings(updates);
      if (!result.ok) {
        toast.show({
          kind: 'error',
          title: 'Save failed',
          message: String(result.body?.error || result.error || 'save failed'),
          durationMs: 8000,
        });
        return;
      }
      toast.show({
        kind: 'success',
        title: 'Saved',
        message: result.body?.message
          || 'Restart kato for the change to take effect.',
        durationMs: 7000,
      });
      setSavedAt(Date.now());
      refresh();
    } finally {
      setSaving(false);
    }
  }

  function revert() {
    if (!section) { return; }
    const seed = {};
    for (const f of section.fields) { seed[f.key] = f.value ?? ''; }
    setDraft(seed);
  }

  if (state.loading) {
    return (
      <div className="settings-drawer-panel">
        <p className="settings-drawer-message">Loading settings…</p>
      </div>
    );
  }
  if (state.error) {
    return (
      <div className="settings-drawer-panel">
        <p className="settings-drawer-message is-error">{state.error}</p>
      </div>
    );
  }
  if (!section) {
    return (
      <div className="settings-drawer-panel">
        <p className="settings-drawer-message">Unknown settings section.</p>
      </div>
    );
  }

  return (
    <div className="settings-drawer-panel">
      <header className="settings-drawer-panel-head">
        <h3>{section.title || section.label}</h3>
        <p>
          {section.description}
          {' '}Saved to
          {' '}<code>{state.settingsFilePath || '~/.kato/settings.json'}</code>
          {' '}— your <code>.env</code> is left untouched (read as a
          fallback).
        </p>
      </header>

      {section.warning && (
        <div className="settings-drawer-section-warning">
          ⚠ {section.warning}
        </div>
      )}

      <div className="settings-drawer-fields">
        {section.fields.map((f) => (
          <SchemaField
            key={f.key}
            field={f}
            value={draft[f.key] ?? ''}
            onChange={(v) => setField(f.key, v)}
          />
        ))}
      </div>

      <div className="settings-drawer-actions">
        <button
          type="button"
          className="settings-drawer-action-secondary"
          onClick={revert}
          disabled={saving || dirtyKeys.length === 0}
        >
          Revert
        </button>
        <button
          type="button"
          className="settings-drawer-action-primary"
          onClick={save}
          disabled={saving || dirtyKeys.length === 0}
        >
          {saving
            ? 'Saving…'
            : (dirtyKeys.length
              ? `Save ${dirtyKeys.length} change${dirtyKeys.length === 1 ? '' : 's'}`
              : 'Save')}
        </button>
      </div>

      {savedAt && (
        <div className="settings-drawer-restart-banner">
          ⚠ Restart kato for the change to take effect.
        </div>
      )}
    </div>
  );
}


function SchemaField({ field, value, onChange }) {
  const isBool = field.type === 'bool';
  const isSelect = field.type === 'select';
  const isSecret = field.type === 'secret';
  const isNumber = field.type === 'number';
  const boolChecked = String(value).toLowerCase() === 'true';
  const [tipPos, setTipPos] = useState(null);

  const tipText = [field.help, field.warning && `⚠ ${field.warning}`, field.danger && `⛔ ${field.danger}`].filter(Boolean).join('\n\n');

  const showTip = useCallback((e) => {
    const r = e.currentTarget.getBoundingClientRect();
    setTipPos({ x: r.left + r.width / 2, y: r.top });
  }, []);
  const hideTip = useCallback(() => setTipPos(null), []);

  return (
    <>
    <label
      className={[
        'settings-drawer-field',
        field.danger ? 'is-danger' : '',
        isBool ? 'is-toggle-row' : '',
      ].filter(Boolean).join(' ')}
    >
      <span className="settings-drawer-field-label">
        <code>{field.key}</code>
        <span className="settings-drawer-field-name">{field.label}</span>
        {field.source && (
          <span className={`settings-drawer-source source-${field.source}`}>
            {sourceLabel(field.source)}
          </span>
        )}
        {tipText && (
          <span
            className="settings-drawer-field-info"
            tabIndex={0}
            role="img"
            aria-label="Field info"
            onMouseEnter={showTip}
            onMouseLeave={hideTip}
            onFocus={showTip}
            onBlur={hideTip}
          >
            ⓘ
          </span>
        )}
      </span>

      {isBool ? (
        <input
          type="checkbox"
          className="settings-drawer-toggle"
          checked={boolChecked}
          onChange={(e) => onChange(e.target.checked ? 'true' : 'false')}
        />
      ) : isSelect ? (
        <select
          className="settings-drawer-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          {(field.options || []).map((opt) => (
            <option key={opt} value={opt}>
              {opt === '' ? '(default)' : opt}
            </option>
          ))}
        </select>
      ) : (
        <input
          type={isSecret ? 'password' : (isNumber ? 'number' : 'text')}
          className="settings-drawer-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={isSecret && value ? '(set — paste to replace)' : ''}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
        />
      )}

      {field.help && (
        <span className="settings-drawer-field-hint">{field.help}</span>
      )}
      {field.warning && (
        <span className="settings-drawer-field-warning">⚠ {field.warning}</span>
      )}
      {field.danger && (
        <span className="settings-drawer-field-danger">⛔ {field.danger}</span>
      )}
    </label>
    {tipPos && tipText && createPortal(
      <div
        className="settings-field-tooltip"
        style={{ left: tipPos.x, top: tipPos.y }}
      >
        {tipText}
      </div>,
      document.body
    )}
    </>
  );
}
