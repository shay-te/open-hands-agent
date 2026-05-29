import { useCallback, useEffect, useState } from 'react';
import { fetchTaskProviders, updateTaskProvider } from '../api.js';
import { toast } from '../stores/toastStore.js';
import { apiErrorMessage } from '../utils/apiError.js';
import { sourceLabel } from '../utils/settingsSource.js';
import { isSecretKey, buildDraftFor } from '../utils/providerFields.js';
import PanelMessage from './settings/PanelMessage.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';
import SettingsActions from './settings/SettingsActions.jsx';
import RestartBanner from './settings/RestartBanner.jsx';

// "Task provider" tab — where tickets live + which platform kato
// polls. The "Active provider" dropdown writes ``KATO_ISSUE_PLATFORM``;
// the fields below are that platform's full env set (connection +
// issue scoping + state transitions). Saved to
// ``~/.kato/settings.json`` (the operator's ``.env`` is left
// untouched — kato still reads it as a fallback). Restart required
// since kato reads the env at boot.

const PROVIDER_LABELS = {
  youtrack: 'YouTrack',
  jira: 'Jira',
  github: 'GitHub Issues',
  gitlab: 'GitLab Issues',
  bitbucket: 'Bitbucket Issues',
};

export default function TaskProviderSettingsPanel() {
  const [state, setState] = useState({
    loading: true,
    error: '',
    active: '',
    supported: [],
    providers: {},
    settingsFilePath: '',
  });
  const [selected, setSelected] = useState('');
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(null);

  const refresh = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: '' }));
    const result = await fetchTaskProviders();
    if (!result.ok) {
      setState({
        loading: false,
        error: apiErrorMessage(result, 'load failed'),
        active: '', supported: [], providers: {}, settingsFilePath: '',
      });
      return;
    }
    const body = result.body || {};
    const supported = Array.isArray(body.supported) ? body.supported : [];
    const active = String(body.active || supported[0] || 'youtrack');
    setState({
      loading: false,
      error: '',
      active,
      supported,
      providers: body.providers || {},
      settingsFilePath: String(body.settings_file_path || body.env_file_path || ''),
    });
    setSelected((current) => current || active);
    setDraft(buildDraftFor(body.providers || {}, (selected || active)));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (!selected) { return; }
    setDraft(buildDraftFor(state.providers, selected));
  }, [selected, state.providers]);

  const isDirty = Object.entries(draft).some(([key, value]) => {
    const serverValue = state.providers?.[selected]?.fields?.[key]?.value || '';
    return value !== serverValue;
  }) || (selected && selected !== state.active);

  async function save() {
    setSaving(true);
    try {
      const result = await updateTaskProvider({
        active: selected,
        provider: selected,
        fields: draft,
      });
      if (!result.ok) {
        toast.show({
          kind: 'error',
          title: 'Save failed',
          message: apiErrorMessage(result, 'save failed'),
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

  const fields = state.providers?.[selected]?.fields || {};

  return (
    <div className="settings-drawer-panel">
      <SettingsPanelHead title="Task provider">
        <p>
          Where tickets live + which platform kato polls for assigned
          work. The dropdown sets <code>KATO_ISSUE_PLATFORM</code>;
          fields are saved to
          {' '}<code>{state.settingsFilePath || '~/.kato/settings.json'}</code>
          {' '}(your <code>.env</code> is left untouched — kato still
          reads it as a fallback).
        </p>
      </SettingsPanelHead>

      {state.loading && (
        <PanelMessage>Loading task providers…</PanelMessage>
      )}
      {state.error && (
        <PanelMessage error>{state.error}</PanelMessage>
      )}

      {!state.loading && !state.error && (
        <>
          <label className="settings-drawer-field">
            <span className="settings-drawer-field-label">Active provider</span>
            <select
              className="settings-drawer-input"
              value={selected || state.active}
              onChange={(ev) => setSelected(ev.target.value)}
            >
              {state.supported.map((name) => (
                <option key={name} value={name}>
                  {PROVIDER_LABELS[name] || name}
                </option>
              ))}
            </select>
            <span className="settings-drawer-field-hint">
              The other providers' fields stay editable — switch
              between them with this dropdown.
            </span>
          </label>

          <div className="settings-drawer-divider" />

          <div className="settings-drawer-fields">
            {Object.keys(fields).map((key) => {
              const f = fields[key] || {};
              const isSecret = isSecretKey(key);
              const placeholder = isSecret && f.value
                ? '(set — paste again to replace)'
                : '';
              return (
                <label key={key} className="settings-drawer-field">
                  <span className="settings-drawer-field-label">
                    <code>{key}</code>
                    <span className={`settings-drawer-source source-${f.source || 'unset'}`}>
                      {sourceLabel(f.source)}
                    </span>
                  </span>
                  <input
                    type={isSecret ? 'password' : 'text'}
                    className="settings-drawer-input"
                    value={draft[key] || ''}
                    onChange={(ev) =>
                      setDraft((current) => ({ ...current, [key]: ev.target.value }))
                    }
                    placeholder={placeholder}
                    spellCheck={false}
                    autoComplete="off"
                    autoCapitalize="off"
                    autoCorrect="off"
                  />
                </label>
              );
            })}
          </div>

          <SettingsActions
            onSecondary={refresh}
            secondaryLabel="Revert"
            onSave={save}
            saving={saving}
            canSave={isDirty}
            primaryLabel="Save"
          />

          <RestartBanner show={savedAt} />
        </>
      )}
    </div>
  );
}
