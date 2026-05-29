import { useEffect, useMemo, useState } from 'react';
import { addTaskRepository, fetchInventoryRepositories } from '../api.js';
import { toast } from '../stores/toastStore.js';
import { apiErrorMessage } from '../utils/apiError.js';
import ModalShell from './ModalShell.jsx';
import ModalFooterActions from './ModalFooterActions.jsx';

// "+ Add repository" picker for the Files tab. Lists every repo in
// kato's inventory, filters out ones already on the task, lets the
// operator pick one, and on confirm runs the platform-side tag write
// + workspace clone in one server call.
//
// ``alreadyAttachedIds`` is the lower-cased set of repo ids the
// current task already has — typically the entries in the file
// tree's repo list. We filter UI-side so the picker is responsive
// even on slow ticket-platform connections; the server still
// validates against the inventory before tagging.
export default function AddRepositoryModal({
  taskId,
  alreadyAttachedIds = new Set(),
  onClose,
  onAdded,
}) {
  const [query, setQuery] = useState('');
  const [repositories, setRepositories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    fetchInventoryRepositories()
      .then((result) => {
        if (cancelled) { return; }
        if (!result.ok) {
          setError(apiErrorMessage(result, 'failed to load repositories'));
          setRepositories([]);
          return;
        }
        const list = Array.isArray(result.body?.repositories)
          ? result.body.repositories
          : [];
        setRepositories(list);
      })
      .finally(() => {
        if (!cancelled) { setLoading(false); }
      });
    return () => { cancelled = true; };
  }, []);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const attached = new Set(
      Array.from(alreadyAttachedIds || []).map((id) => String(id).toLowerCase()),
    );
    return (repositories || []).filter((repo) => {
      const id = String(repo.id || '').toLowerCase();
      if (attached.has(id)) { return false; }
      if (!needle) { return true; }
      const haystack = `${id}\n${repo.owner || ''}\n${repo.repo_slug || ''}`
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [repositories, query, alreadyAttachedIds]);

  async function onConfirm() {
    if (!selectedId || adding) { return; }
    setAdding(true);
    const result = await addTaskRepository(taskId, selectedId);
    setAdding(false);
    if (!result.ok) {
      toast.show({
        kind: 'error',
        title: 'Could not add repository',
        message: apiErrorMessage(result, 'add failed'),
        durationMs: 12000,
      });
      return;
    }
    const body = result.body || {};
    const tagAdded = body.tag_added;
    const sync = body.sync || {};
    const cloned = (sync.added_repositories || []).length;
    const lines = [];
    if (tagAdded) {
      lines.push(`✓ tagged task with ${body.tag_name}`);
    } else {
      lines.push(`• tag ${body.tag_name} was already on the task`);
    }
    if (cloned > 0) {
      lines.push(`✓ cloned ${cloned} repo(s): ${(sync.added_repositories || []).join(', ')}`);
    } else if (sync.failed_repositories && sync.failed_repositories.length) {
      const errs = sync.failed_repositories
        .map((entry) => `${entry.repository_id}: ${entry.error}`).join('; ');
      lines.push(`✗ clone failed: ${errs}`);
    } else {
      lines.push('• repository was already cloned');
    }
    toast.show({
      kind: cloned > 0 || tagAdded ? 'success' : 'warning',
      title: 'Add repository',
      message: lines.join('\n'),
      durationMs: 8000,
    });
    if (typeof onAdded === 'function') { onAdded(body); }
    onClose();
  }

  return (
    <ModalShell
      ariaLabel="Add repository to task"
      title={<>Add repository to {taskId}</>}
      onClose={onClose}
    >
        <p className="adopt-session-modal-help">
          Pick a repository from kato's inventory. Kato will tag the
          task with <code>kato:repo:&lt;id&gt;</code> and clone the
          repo into this task's workspace. Repositories already
          attached to the task are filtered out.
        </p>
        <input
          type="text"
          className="adopt-session-search"
          placeholder="Search by id, owner, or slug…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          autoFocus
        />
        <div className="adopt-session-list">
          {loading && (
            <div className="adopt-session-empty">Loading repositories…</div>
          )}
          {!loading && error && (
            <div className="adopt-session-empty adopt-session-error">
              {error}
            </div>
          )}
          {!loading && !error && filtered.length === 0 && (
            <div className="adopt-session-empty">
              {repositories.length === 0
                ? 'No repositories configured in kato.'
                : 'Every repository in the inventory is already attached '
                  + 'to this task.'}
            </div>
          )}
          {!loading && !error && filtered.map((repo) => {
            const isSelected = repo.id === selectedId;
            return (
              <button
                type="button"
                key={repo.id}
                className={[
                  'adopt-session-row',
                  isSelected ? 'is-selected' : '',
                ].filter(Boolean).join(' ')}
                onClick={() => setSelectedId(repo.id)}
              >
                <div className="adopt-session-row-top">
                  <span className="adopt-session-cwd">{repo.id}</span>
                  <span className="adopt-session-meta">
                    {repo.owner && repo.repo_slug
                      ? `${repo.owner}/${repo.repo_slug}`
                      : (repo.owner || repo.repo_slug || '')}
                  </span>
                </div>
                {repo.local_path && (
                  <div className="adopt-session-preview">
                    {repo.local_path}
                  </div>
                )}
              </button>
            );
          })}
        </div>
        <ModalFooterActions
          onCancel={onClose}
          onConfirm={onConfirm}
          busy={adding}
          canConfirm={Boolean(selectedId)}
          confirmLabel="Add to task"
          busyLabel="Adding…"
        />
    </ModalShell>
  );
}
