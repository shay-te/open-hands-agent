import { useEffect, useRef } from 'react';
import { TAB_STATUS } from '../constants/tabStatus.js';
import { deriveTabStatus } from '../utils/tabStatus.js';
import { useEscapeKey } from '../hooks/useEscapeKey.js';
import DialogShell from './DialogShell.jsx';

/**
 * Hard-confirm dialog for the tab "X" (forget) button.
 *
 * Forgetting a task is destructive and irreversible — it wipes the
 * local clone — so we replace the old native ``window.confirm`` with
 * a designed modal that spells out *exactly* what is lost and forces
 * an explicit click on the danger button. Cancel is the default
 * (auto-focused); Esc and a backdrop click both cancel.
 *
 * The consequence list is context-aware: an in-review task or one
 * with un-pushed changes gets an extra red callout, because that's
 * the case where forgetting actually destroys unrecoverable work.
 */
export default function ForgetTaskModal({ session, onConfirm, onCancel }) {
  const cancelRef = useRef(null);

  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  useEscapeKey(onCancel);

  if (!session) { return null; }

  const taskId = String(session.task_id || '').trim() || 'this task';
  const baseStatus = deriveTabStatus(session);
  const inReview = baseStatus === TAB_STATUS.REVIEW;
  const hasUnpushed = !!session.has_changes_pending;

  return (
    <DialogShell
      id="forget-task-modal"
      ariaLabelledBy="forget-task-title"
      title="Forget task?"
      subtitle={taskId}
      subtitleId="forget-task-name"
      onClose={onCancel}
      backdropClose
    >
        <p className="forget-task-lead">
          This permanently deletes kato&rsquo;s local copy of{' '}
          <strong>{taskId}</strong>. It cannot be undone. Here&rsquo;s
          what happens:
        </p>

        <ul className="forget-task-effects">
          <li>
            The per-task workspace clone
            (<code>~/.kato/workspaces/{taskId}/</code> and every
            repository cloned into it) is deleted from disk.
          </li>
          <li>
            Any commits or edits <strong>not already pushed to a pull
            request are lost</strong> — there is no local backup.
          </li>
          <li>
            This tab and its local chat view are removed; the Claude
            session created for this task is dropped.
          </li>
          <li>
            The ticket itself on the issue tracker is{' '}
            <strong>not</strong> changed. If it&rsquo;s still in a
            state kato watches, kato may re-clone it fresh on a later
            scan.
          </li>
        </ul>

        {(inReview || hasUnpushed) && (
          <p className="forget-task-warning" role="alert">
            {inReview && (
              <>
                ⚠ This task is <strong>in review (&ldquo;To
                Verify&rdquo;)</strong> — you may still be verifying
                it. Forgetting it now throws away the local clone and
                any fixes that haven&rsquo;t been pushed.{' '}
              </>
            )}
            {hasUnpushed && (
              <>
                ⚠ This task has <strong>changes ready to push that
                haven&rsquo;t been pushed yet</strong> — they will be
                gone for good.
              </>
            )}
          </p>
        )}

        <div className="modal-actions">
          <button
            id="forget-task-cancel"
            type="button"
            className="secondary"
            ref={cancelRef}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            id="forget-task-confirm"
            type="button"
            className="danger"
            onClick={onConfirm}
          >
            Forget {taskId}
          </button>
        </div>
    </DialogShell>
  );
}
