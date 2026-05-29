import { useEffect, useMemo, useState } from 'react';
import { adoptTask, fetchAllAssignedTasks } from '../api.js';
import { toast } from '../stores/toastStore.js';
import { apiErrorMessage } from '../utils/apiError.js';
import ModalShell from './ModalShell.jsx';
import ModalFooterActions from './ModalFooterActions.jsx';

// Left-panel "+ Add task" picker. Lists every task assigned to
// kato (open, in progress, in review, done), filters by id /
// summary as the operator types, and on confirm calls
// ``/api/tasks/<id>/adopt`` to provision the workspace + clones.
//
// Tasks already on the left panel (i.e., kato already has a
// workspace for them) are shown but greyed out — re-adopting is a
// no-op so we let the operator do it for "I want to refresh this"
// rather than forbidding it; the workspace_manager.create call is
// idempotent.
export default function AdoptTaskModal({
  alreadyAdoptedIds = new Set(),
  onClose,
  onAdopted,
}) {
  const [query, setQuery] = useState('');
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [adopting, setAdopting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    fetchAllAssignedTasks().then((result) => {
      if (cancelled) { return; }
      if (!result.ok) {
        setError(String(result.error || 'failed to load tasks'));
        setTasks([]);
        return;
      }
      setTasks(Array.isArray(result.body?.tasks) ? result.body.tasks : []);
    }).finally(() => {
      if (!cancelled) { setLoading(false); }
    });
    return () => { cancelled = true; };
  }, []);

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

  async function onConfirm() {
    if (!selectedId || adopting) { return; }
    setAdopting(true);
    const result = await adoptTask(selectedId);
    setAdopting(false);
    if (!result.ok) {
      const err = apiErrorMessage(result, 'adopt failed');
      toast.show({
        kind: 'error',
        title: 'Could not adopt task',
        message: err,
        durationMs: 12000,
      });
      return;
    }
    const body = result.body || {};
    const cloned = body.cloned_repositories || [];
    toast.show({
      kind: 'success',
      title: `Adopted ${body.task_id || selectedId}`,
      message: cloned.length
        ? `✓ cloned ${cloned.length} repo(s): ${cloned.join(', ')}`
        : '✓ workspace ready (no repositories required cloning)',
      durationMs: 8000,
    });
    if (typeof onAdopted === 'function') { onAdopted(body); }
    onClose();
  }

  return (
    <ModalShell
      ariaLabel="Adopt task"
      title="Adopt a task"
      extraClass="adopt-task-modal"
      onClose={onClose}
    >
        <p className="adopt-session-modal-help">
          Pick a task assigned to kato. Kato will provision a per-task
          workspace and clone every repository the task touches (driven
          by <code>kato:repo:&lt;id&gt;</code> tags + the description).
          Tasks in any state are listed — open, in progress, in review,
          done.
        </p>
        <input
          type="text"
          className="adopt-session-search"
          placeholder="Search by id, summary, or state…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          autoFocus
        />
        <div className="adopt-session-list">
          {loading && (
            <div className="adopt-session-empty">Loading tasks…</div>
          )}
          {!loading && error && (
            <div className="adopt-session-empty adopt-session-error">
              {error}
            </div>
          )}
          {!loading && !error && filtered.length === 0 && (
            <div className="adopt-session-empty">
              {tasks.length === 0
                ? 'No tasks assigned to kato in your ticket platform.'
                : `No tasks match “${query}”.`}
            </div>
          )}
          {!loading && !error && filtered.map((task) => {
            const isSelected = task.id === selectedId;
            const alreadyAdopted = alreadyAdoptedIds.has(task.id);
            return (
              <button
                type="button"
                key={task.id}
                className={[
                  'adopt-session-row',
                  isSelected ? 'is-selected' : '',
                  alreadyAdopted ? 'is-already-adopted' : '',
                ].filter(Boolean).join(' ')}
                onClick={() => setSelectedId(task.id)}
              >
                <div className="adopt-session-row-top">
                  <span className="adopt-session-cwd">{task.id}</span>
                  <span className="adopt-session-meta">
                    {task.state || ''}
                    {alreadyAdopted ? ' · already in kato' : ''}
                  </span>
                </div>
                {task.summary && (
                  <div className="adopt-session-preview">{task.summary}</div>
                )}
              </button>
            );
          })}
        </div>
        <ModalFooterActions
          onCancel={onClose}
          onConfirm={onConfirm}
          busy={adopting}
          canConfirm={Boolean(selectedId)}
          confirmLabel="Adopt task"
          busyLabel="Adopting…"
        />
    </ModalShell>
  );
}
