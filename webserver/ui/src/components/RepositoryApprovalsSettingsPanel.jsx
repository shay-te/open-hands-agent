import { useCallback, useEffect, useState } from 'react';
import {
  fetchRepositoryApprovals,
  updateRepositoryApprovals,
} from '../api.js';
import { toast } from '../stores/toastStore.js';
import { apiErrorMessage } from '../utils/apiError.js';
import PanelMessage from './settings/PanelMessage.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';
import SettingsActions from './settings/SettingsActions.jsx';

// "Repository approvals" tab — the UI replacement for the old
// ``./kato approve-repo`` CLI picker. Lists every candidate kato
// discovers (inventory + checkout + workspace clones), shows which
// are approved + their mode, and lets the operator toggle approve /
// revoke + restricted ↔ trusted in batch.
//
// Save sends one POST with the diff against the loaded state, so a
// rapid toggle (approve → revoke → approve) ends with one network
// trip in the right final state.

const MODE_RESTRICTED = 'restricted';
const MODE_TRUSTED = 'trusted';

export default function RepositoryApprovalsSettingsPanel() {
  const [state, setState] = useState({
    loading: true,
    error: '',
    rows: [],
    storagePath: '',
  });
  // Per-row pending edits keyed by repository_id. Each value is
  // ``{ approved: bool, mode: 'restricted' | 'trusted' }``. We
  // compare against the original row on save to compute the
  // approve / revoke arrays.
  const [edits, setEdits] = useState({});
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: '' }));
    const result = await fetchRepositoryApprovals();
    if (!result.ok) {
      setState({
        loading: false,
        error: apiErrorMessage(result, 'load failed'),
        rows: [],
        storagePath: '',
      });
      return;
    }
    setState({
      loading: false,
      error: '',
      rows: Array.isArray(result.body?.repositories) ? result.body.repositories : [],
      storagePath: String(result.body?.storage_path || ''),
    });
    setEdits({});
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  function rowState(row) {
    const e = edits[row.repository_id];
    if (e) { return e; }
    return {
      approved: !!row.approved,
      mode: row.approval_mode || MODE_RESTRICTED,
    };
  }

  function patchEdit(repoId, patch) {
    setEdits((current) => ({
      ...current,
      [repoId]: { ...rowStateFor(current, state.rows, repoId), ...patch },
    }));
  }

  const hasChanges = state.rows.some((row) => {
    const e = edits[row.repository_id];
    if (!e) { return false; }
    if ((!!e.approved) !== !!row.approved) { return true; }
    if (e.approved && e.mode !== (row.approval_mode || MODE_RESTRICTED)) {
      return true;
    }
    return false;
  });

  async function save() {
    const approve = [];
    const revoke = [];
    for (const row of state.rows) {
      const e = edits[row.repository_id];
      if (!e) { continue; }
      const wasApproved = !!row.approved;
      const isApproved = !!e.approved;
      if (!wasApproved && isApproved) {
        approve.push({
          repository_id: row.repository_id,
          remote_url: row.remote_url || row.approved_remote_url,
          mode: e.mode,
        });
      } else if (wasApproved && !isApproved) {
        revoke.push(row.repository_id);
      } else if (wasApproved && isApproved && e.mode !== row.approval_mode) {
        approve.push({
          repository_id: row.repository_id,
          remote_url: row.remote_url || row.approved_remote_url,
          mode: e.mode,
        });
      }
    }
    if (approve.length === 0 && revoke.length === 0) { return; }
    setSaving(true);
    try {
      const result = await updateRepositoryApprovals({ approve, revoke });
      if (!result.ok) {
        toast.show({
          kind: 'error',
          title: 'Save failed',
          message: apiErrorMessage(result, 'save failed'),
          durationMs: 8000,
        });
        return;
      }
      const counts = result.body?.applied || {};
      toast.show({
        kind: 'success',
        title: 'Saved',
        message: `${(counts.approved || []).length} approved, `
          + `${(counts.revoked || []).length} revoked.`,
        durationMs: 5000,
      });
      refresh();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings-drawer-panel">
      <SettingsPanelHead title="Repository approvals">
        <p>
          Kato refuses tasks on repos that aren't on this list. Toggle
          to approve, pick <strong>restricted</strong> (re-check the
          remote URL at runtime) or <strong>trusted</strong> (skip the
          recheck). Stored in <code>{state.storagePath || '~/.kato/approved-repositories.json'}</code>.
        </p>
      </SettingsPanelHead>

      {state.loading && (
        <PanelMessage>Loading repositories…</PanelMessage>
      )}
      {state.error && (
        <PanelMessage error>{state.error}</PanelMessage>
      )}

      {!state.loading && !state.error && (
        <>
          {state.rows.length === 0 ? (
            <p className="settings-drawer-message">
              No repositories discovered yet. Set
              <code> REPOSITORY_ROOT_PATH </code>
              in the Repositories tab, or configure the
              <code> repositories: </code>
              block in your kato config.
            </p>
          ) : (
            <table className="settings-drawer-approvals-table">
              <thead>
                <tr>
                  <th>Approved</th>
                  <th>Repository</th>
                  <th>Mode</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {state.rows.map((row) => {
                  const e = rowState(row);
                  return (
                    <tr
                      key={row.repository_id}
                      className={row.remote_url_drift ? 'has-drift' : ''}
                    >
                      <td>
                        <label className="settings-drawer-approval-toggle">
                          <input
                            type="checkbox"
                            checked={e.approved}
                            onChange={(ev) =>
                              patchEdit(row.repository_id, { approved: ev.target.checked })
                            }
                          />
                        </label>
                      </td>
                      <td>
                        <div className="settings-drawer-approval-id">
                          {row.repository_id}
                        </div>
                        <div
                          className="settings-drawer-approval-url"
                          title={row.remote_url}
                        >
                          {row.remote_url || '(no remote)'}
                        </div>
                        {row.remote_url_drift && (
                          <div className="settings-drawer-approval-drift">
                            ⚠ discovered remote differs from approval:
                            {' '}
                            <code>{row.approved_remote_url}</code>
                            {' '}— toggle to re-approve the new URL.
                          </div>
                        )}
                      </td>
                      <td>
                        <select
                          className="settings-drawer-input is-compact"
                          value={e.mode}
                          onChange={(ev) =>
                            patchEdit(row.repository_id, { mode: ev.target.value })
                          }
                          disabled={!e.approved}
                        >
                          <option value={MODE_RESTRICTED}>restricted</option>
                          <option value={MODE_TRUSTED}>trusted</option>
                        </select>
                      </td>
                      <td>
                        <span className={`settings-drawer-source source-${row.source}`}>
                          {row.source}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          <SettingsActions
            onSecondary={refresh}
            secondaryLabel="Refresh"
            onSave={save}
            saving={saving}
            canSave={hasChanges}
            primaryLabel="Save changes"
          />
        </>
      )}
    </div>
  );
}


// Like ``rowState`` but resolved against a snapshot — used inside
// the setEdits updater where the new edit needs the previous state
// without a fresh closure over ``state.rows``.
function rowStateFor(currentEdits, rows, repoId) {
  const e = currentEdits[repoId];
  if (e) { return e; }
  const row = rows.find((r) => r.repository_id === repoId);
  return {
    approved: !!(row && row.approved),
    mode: (row && row.approval_mode) || MODE_RESTRICTED,
  };
}
