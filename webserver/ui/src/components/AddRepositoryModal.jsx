import { useMemo, useState } from 'react';
import { addTaskRepository, fetchInventoryRepositories } from '../api.js';
import { toast } from '../stores/toastStore.js';
import { apiErrorMessage } from '../utils/apiError.js';
import { usePickerData } from '../hooks/usePickerData.js';
import SearchPickerModal from './SearchPickerModal.jsx';

// "+ Add repository" picker for the Files tab. Lists every repo in kato's
// inventory, filters out ones already on the task (client-side), and on
// confirm runs the platform-side tag write + workspace clone in one call.
// Config over the shared <SearchPickerModal>.
export default function AddRepositoryModal({
  taskId,
  alreadyAttachedIds = new Set(),
  onClose,
  onAdded,
}) {
  const [query, setQuery] = useState('');
  const { data: repositories, loading, error } = usePickerData(async () => {
    const result = await fetchInventoryRepositories();
    if (!result.ok) { throw new Error(apiErrorMessage(result, 'failed to load repositories')); }
    return Array.isArray(result.body?.repositories) ? result.body.repositories : [];
  }, [], []);

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

  async function onConfirm(repo) {
    const result = await addTaskRepository(taskId, repo.id);
    if (!result.ok) {
      toast.errorFromResult(result, {
        title: 'Could not add repository',
        fallback: 'add failed',
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
    <SearchPickerModal
      ariaLabel="Add repository to task"
      title={<>Add repository to {taskId}</>}
      onClose={onClose}
      helpText={(
        <>
          Pick a repository from kato's inventory. Kato will tag the
          task with <code>kato:repo:&lt;id&gt;</code> and clone the
          repo into this task's workspace. Repositories already
          attached to the task are filtered out.
        </>
      )}
      searchPlaceholder="Search by id, owner, or slug…"
      query={query}
      onQueryChange={setQuery}
      items={filtered}
      loading={loading}
      error={error}
      loadingText="Loading repositories…"
      emptyText={repositories.length === 0
        ? 'No repositories configured in kato.'
        : 'Every repository in the inventory is already attached to this task.'}
      getItemId={(repo) => repo.id}
      confirmLabel="Add to task"
      busyLabel="Adding…"
      onConfirm={onConfirm}
      renderRow={(repo) => (
        <>
          <div className="adopt-session-row-top">
            <span className="adopt-session-cwd">{repo.id}</span>
            <span className="adopt-session-meta">
              {repo.owner && repo.repo_slug
                ? `${repo.owner}/${repo.repo_slug}`
                : (repo.owner || repo.repo_slug || '')}
            </span>
          </div>
          {repo.local_path && (
            <div className="adopt-session-preview">{repo.local_path}</div>
          )}
        </>
      )}
    />
  );
}
