import { useEffect, useMemo, useState } from 'react';
import { parseDiff, Diff, Hunk } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { fetchRepoCommitDiff } from '../api.js';
import { tokenizeHunks } from '../utils/diffSyntax.js';
import { apiErrorMessage } from '../utils/apiError.js';
import ModalShell from './ModalShell.jsx';

// Inline modal that fetches + renders one commit's unified diff.
// Triggered from the Files-tab per-repo "view commit" dropdown so
// the operator can see exactly what each kato commit changed
// without leaving the file tree.
//
// Uses the same ``react-diff-view`` rendering as ChangesTab so the
// look-and-feel is consistent (intra-line highlights, syntax
// tokens, monospace gutter). Reuses the existing
// ``adopt-session-modal-*`` CSS shell for the chrome — avoids a
// new stylesheet for what is effectively a "show this thing"
// scrim.
export default function CommitDiffModal({ taskId, repoId, commit, onClose }) {
  const [state, setState] = useState({ status: 'loading', diff: '', error: '' });

  useEffect(() => {
    if (!commit?.sha) { return undefined; }
    let cancelled = false;
    setState({ status: 'loading', diff: '', error: '' });
    fetchRepoCommitDiff(taskId, repoId, commit.sha).then((result) => {
      if (cancelled) { return; }
      if (!result.ok) {
        setState({
          status: 'error',
          diff: '',
          error: apiErrorMessage(result, 'failed to load commit diff'),
        });
        return;
      }
      setState({
        status: 'ready',
        diff: String(result.body?.diff || ''),
        error: '',
      });
    });
    return () => { cancelled = true; };
  }, [taskId, repoId, commit?.sha]);

  const files = useMemo(
    () => (state.diff ? parseDiff(state.diff) : []),
    [state.diff],
  );

  const subject = String(commit?.subject || '').trim();
  const author = String(commit?.author || '').trim();
  const shortSha = String(commit?.short_sha || commit?.sha || '').slice(0, 8);

  return (
    <ModalShell
      ariaLabel={`Commit ${shortSha} — ${subject}`}
      title={(
        <>
          <code>{shortSha}</code> — {subject || '(no subject)'}
        </>
      )}
      extraClass="commit-diff-modal"
      onClose={onClose}
    >
      {author && (
        <p className="adopt-session-modal-help">
          <strong>{author}</strong> in <code>{repoId}</code>
          {' · '}
          <code>{String(commit?.sha || '')}</code>
        </p>
      )}
      <div className="commit-diff-modal-body">
        {state.status === 'loading' && (
          <p className="changes-tab-message">Loading commit diff…</p>
        )}
        {state.status === 'error' && (
          <p className="changes-tab-message error">{state.error}</p>
        )}
        {state.status === 'ready' && files.length === 0 && (
          <p className="changes-tab-message">
            This commit has no file changes (or the diff is empty).
          </p>
        )}
        {state.status === 'ready' && files.map((file) => (
          <CommitDiffFile key={diffFileKey(file)} file={file} />
        ))}
      </div>
    </ModalShell>
  );
}


function CommitDiffFile({ file }) {
  const path = file.newPath || file.oldPath || '(unknown)';
  const tokens = useMemo(
    () => tokenizeHunks(file.hunks || [], path),
    [file.hunks, path],
  );
  return (
    <section className="diff-file">
      <header className="diff-file-header">
        <span className="diff-file-type">{file.type}</span>
        <span className="diff-file-path">{path}</span>
      </header>
      <Diff
        viewType="unified"
        diffType={file.type}
        hunks={file.hunks || []}
        tokens={tokens}
      >
        {(hunks) => hunks.map((hunk) => (
          <Hunk key={hunk.content} hunk={hunk} />
        ))}
      </Diff>
    </section>
  );
}


function diffFileKey(file) {
  return `${file.type}:${file.oldPath || ''}->${file.newPath || ''}`;
}
