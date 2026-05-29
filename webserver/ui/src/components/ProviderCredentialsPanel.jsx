import { useEffect, useState } from 'react';
import { useSettingsResource } from '../hooks/useSettingsResource.js';
import { useRestartingSave } from '../hooks/useRestartingSave.js';
import { isSecretKey, buildDraftFor } from '../utils/providerFields.js';
import { sourceLabel } from '../utils/settingsSource.js';
import PanelMessage from './settings/PanelMessage.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';
import SettingsActions from './settings/SettingsActions.jsx';
import RestartBanner from './settings/RestartBanner.jsx';

// Shared credentials panel for the "Task provider" and "Git provider"
// settings tabs — they were ~90% identical (same state shape, fetch +
// draft seeding, dirty diff, save-with-restart, and field-list JSX). The
// only real differences are the API fns, the label map, the default
// provider, the header/select copy, and whether the panel also writes
// the *active* provider: Task provider does (it sets which platform kato
// polls); Git provider infers the host from each repo's remote URL, so
// it only edits creds. ``includeActive`` toggles that one branch.
//
// ``description`` is the lead sentence(s) of the header copy (a node);
// the shared "saved to <path> (.env untouched)" tail is appended here.
export default function ProviderCredentialsPanel({
  fetchFn,
  updateFn,
  labels,
  defaultProvider,
  title,
  description,
  selectLabel,
  selectHint,
  loadingMessage,
  includeActive = false,
}) {
  const [meta, setMeta] = useState({
    active: '', supported: [], providers: {}, settingsFilePath: '',
  });
  const [selected, setSelected] = useState('');
  const [draft, setDraft] = useState({});

  const { loading, error, refresh } = useSettingsResource(fetchFn, (body) => {
    const supported = Array.isArray(body.supported) ? body.supported : [];
    const active = includeActive
      ? String(body.active || supported[0] || defaultProvider)
      : '';
    const fallback = includeActive ? active : (supported[0] || defaultProvider);
    setMeta({
      active,
      supported,
      providers: body.providers || {},
      settingsFilePath: String(body.settings_file_path || body.env_file_path || ''),
    });
    setSelected((current) => current || fallback);
    setDraft(buildDraftFor(body.providers || {}, selected || fallback));
  });

  // Re-seed the draft from the chosen provider's server values whenever
  // the operator switches providers in the dropdown.
  useEffect(() => {
    if (!selected) { return; }
    setDraft(buildDraftFor(meta.providers, selected));
  }, [selected, meta.providers]);

  const isDirty = Object.entries(draft).some(([key, value]) => {
    const serverValue = meta.providers?.[selected]?.fields?.[key]?.value || '';
    return value !== serverValue;
  }) || (includeActive && selected && selected !== meta.active);

  const { saving, savedAt, save } = useRestartingSave(
    () => updateFn(includeActive
      ? { active: selected, provider: selected, fields: draft }
      : { provider: selected, fields: draft }),
    { onSaved: refresh },
  );

  const fields = meta.providers?.[selected]?.fields || {};

  return (
    <div className="settings-drawer-panel">
      <SettingsPanelHead title={title}>
        <p>
          {description}
          {' '}<code>{meta.settingsFilePath || '~/.kato/settings.json'}</code>
          {' '}(your <code>.env</code> is left untouched — kato still
          reads it as a fallback).
        </p>
      </SettingsPanelHead>

      {loading && (
        <PanelMessage>{loadingMessage}</PanelMessage>
      )}
      {error && (
        <PanelMessage error>{error}</PanelMessage>
      )}

      {!loading && !error && (
        <>
          <label className="settings-drawer-field">
            <span className="settings-drawer-field-label">{selectLabel}</span>
            <select
              className="settings-drawer-input"
              value={includeActive ? (selected || meta.active) : selected}
              onChange={(ev) => setSelected(ev.target.value)}
            >
              {meta.supported.map((name) => (
                <option key={name} value={name}>
                  {labels[name] || name}
                </option>
              ))}
            </select>
            <span className="settings-drawer-field-hint">{selectHint}</span>
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
