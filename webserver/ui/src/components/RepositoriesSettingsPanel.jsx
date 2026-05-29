import { useState } from 'react';
import { fetchSettings, updateSettings } from '../api.js';
import { toast } from '../stores/toastStore.js';
import { useRestartingSave } from '../hooks/useRestartingSave.js';
import { useSettingsResource } from '../hooks/useSettingsResource.js';
import { sourceLabelVerbose } from '../utils/settingsSource.js';
import PanelMessage from './settings/PanelMessage.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';
import SettingsActions from './settings/SettingsActions.jsx';
import RestartBanner from './settings/RestartBanner.jsx';

// "Repositories" tab inside the SettingsDrawer. Operator-editable
// REPOSITORY_ROOT_PATH — the folder kato walks for ``.git`` to
// auto-discover repos.
//
// Saved to ``~/.kato/settings.json`` via POST /api/settings. The
// operator's ``<repo>/.env`` is left untouched (kato still reads it
// as a fallback). The change is load-bearing at boot, so we surface
// "restart required" prominently after every successful save.

export default function RepositoriesSettingsPanel() {
  const [meta, setMeta] = useState({ value: '', source: 'unset', settingsFilePath: '' });
  const [draft, setDraft] = useState('');

  const { loading, error, refresh } = useSettingsResource(fetchSettings, (body) => {
    const repo = body?.repository_root_path || {};
    setMeta({
      value: String(repo.value || ''),
      source: String(repo.source || 'unset'),
      settingsFilePath: String(body?.settings_file_path || body?.env_file_path || ''),
    });
    setDraft(String(repo.value || ''));
  });

  const { saving, savedAt, save: saveRoot } = useRestartingSave(
    () => updateSettings({ repository_root_path: draft.trim() }),
    { onSaved: refresh },
  );

  // Keep the empty-path pre-check as a caller-side guard.
  const save = () => {
    if (!draft.trim()) {
      toast.show({
        kind: 'error',
        title: 'Empty path',
        message: 'Enter a folder path before saving.',
      });
      return;
    }
    return saveRoot();
  };

  const dirty = draft.trim() !== meta.value;
  const sourceLabel = sourceLabelVerbose(meta.source);

  return (
    <div className="settings-drawer-panel">
      <SettingsPanelHead title="Repositories">
        <p>
          The folder kato walks for ``.git`` directories to
          auto-discover repos. Saved to
          {' '}<code>{meta.settingsFilePath || '~/.kato/settings.json'}</code>
          {' '}as <code>REPOSITORY_ROOT_PATH</code> (your <code>.env</code>
          {' '}is left untouched — kato still reads it as a fallback).
        </p>
      </SettingsPanelHead>

      {loading && (
        <PanelMessage>Loading current setting…</PanelMessage>
      )}
      {error && (
        <PanelMessage error>{error}</PanelMessage>
      )}

      {!loading && !error && (
        <>
          <label className="settings-drawer-field">
            <span className="settings-drawer-field-label">Folder path</span>
            <input
              type="text"
              className="settings-drawer-input"
              placeholder="/Users/you/projects"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
            />
            <span className="settings-drawer-field-hint">
              Tip: paste an absolute path, or <code>~/Projects</code> —
              kato expands ``~`` and resolves relative segments on save.
            </span>
          </label>

          <div className="settings-drawer-status-row">
            <span className="settings-drawer-kv">
              <span className="settings-drawer-kv-key">Current</span>
              <code className="settings-drawer-kv-value">
                {meta.value || '(unset)'}
              </code>
            </span>
            <span className="settings-drawer-kv">
              <span className="settings-drawer-kv-key">Source</span>
              <span className={`settings-drawer-kv-value source-${meta.source}`}>
                {sourceLabel}
              </span>
            </span>
          </div>

          <SettingsActions
            onSecondary={() => setDraft(meta.value)}
            secondaryDisabled={!dirty || saving}
            onSave={save}
            saving={saving}
            canSave={dirty}
          />

          <RestartBanner show={savedAt} />
        </>
      )}
    </div>
  );
}
