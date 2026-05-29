import { useMemo, useState } from 'react';
import { adoptTask, fetchAllAssignedTasks } from '../api.js';
import { toast } from '../stores/toastStore.js';
import { apiErrorMessage } from '../utils/apiError.js';
import { usePickerData } from '../hooks/usePickerData.js';
import SearchPickerModal from './SearchPickerModal.jsx';

// Left-panel "+ Add task" picker. Lists every task assigned to kato,
// filters by id / summary / state as the operator types (client-side),
// and on confirm provisions the workspace + clones. Tasks already on the
// left panel are shown but greyed (re-adopting is an idempotent no-op).
// Config over the shared <SearchPickerModal>.
export default function AdoptTaskModal({
  alreadyAdoptedIds = new Set(),
  onClose,
  onAdopted,
}) {
  const [query, setQuery] = useState('');
  const { data: tasks, loading, error } = usePickerData(async () => {
    const result = await fetchAllAssignedTasks();
    if (!result.ok) { throw new Error(String(result.error || 'failed to load tasks')); }
    return Array.isArray(result.body?.tasks) ? result.body.tasks : [];
  }, [], []);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) { return tasks; }
    return tasks.filter((task) => {
      const haystack = (
        `${task.id || ''}\n${task.summary || ''}\n${task.state || ''}`
      ).toLowerCase();
      return haystack.includes(needle);
    });
  }, [tasks, query]);

  async function onConfirm(task) {
    const result = await adoptTask(task.id);
    if (!result.ok) {
      toast.show({
        kind: 'error',
        title: 'Could not adopt task',
        message: apiErrorMessage(result, 'adopt failed'),
        durationMs: 12000,
      });
      return;
    }
    const body = result.body || {};
    const cloned = body.cloned_repositories || [];
    toast.show({
      kind: 'success',
      title: `Adopted ${body.task_id || task.id}`,
      message: cloned.length
        ? `✓ cloned ${cloned.length} repo(s): ${cloned.join(', ')}`
        : '✓ workspace ready (no repositories required cloning)',
      durationMs: 8000,
    });
    if (typeof onAdopted === 'function') { onAdopted(body); }
    onClose();
  }

  return (
    <SearchPickerModal
      ariaLabel="Adopt task"
      title="Adopt a task"
      extraClass="adopt-task-modal"
      onClose={onClose}
      helpText={(
        <>
          Pick a task assigned to kato. Kato will provision a per-task
          workspace and clone every repository the task touches (driven
          by <code>kato:repo:&lt;id&gt;</code> tags + the description).
          Tasks in any state are listed — open, in progress, in review,
          done.
        </>
      )}
      searchPlaceholder="Search by id, summary, or state…"
      query={query}
      onQueryChange={setQuery}
      items={filtered}
      loading={loading}
      error={error}
      loadingText="Loading tasks…"
      emptyText={tasks.length === 0
        ? 'No tasks assigned to kato in your ticket platform.'
        : `No tasks match “${query}”.`}
      getItemId={(task) => task.id}
      rowClassName={(task) => (alreadyAdoptedIds.has(task.id) ? 'is-already-adopted' : '')}
      confirmLabel="Adopt task"
      busyLabel="Adopting…"
      onConfirm={onConfirm}
      renderRow={(task) => (
        <>
          <div className="adopt-session-row-top">
            <span className="adopt-session-cwd">{task.id}</span>
            <span className="adopt-session-meta">
              {task.state || ''}
              {alreadyAdoptedIds.has(task.id) ? ' · already in kato' : ''}
            </span>
          </div>
          {task.summary && (
            <div className="adopt-session-preview">{task.summary}</div>
          )}
        </>
      )}
    />
  );
}
