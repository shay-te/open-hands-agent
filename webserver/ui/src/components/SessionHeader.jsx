import { useState } from 'react';
import {
  finishTask,
  mergeDefaultBranch,
  postChatMessage,
  postSession,
  triggerScan,
  updateTaskSource,
} from '../api.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { TAB_STATUS } from '../constants/tabStatus.js';
import { usePushApproval } from '../hooks/usePushApproval.js';
import { useTaskPublish } from '../hooks/useTaskPublish.js';
import { deriveTabStatus, resolveTabStatus, tabStatusTitle } from '../utils/tabStatus.js';
import { SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import { toast } from '../stores/toastStore.js';
import AdoptSessionModal from './AdoptSessionModal.jsx';
import Icon from './Icon.jsx';
import {
  formatFinishResult,
  formatPullResult,
  formatUpdateSourceResult,
} from './sessionHeaderFormatters.js';

export default function SessionHeader({
  session,
  needsAttention = false,
  onStopped,
  onResume,
  onSessionAdopted,
  streamLifecycle,
  turnInFlight = false,
  searchSlot = null,
}) {
  const [stopping, setStopping] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [updatingSource, setUpdatingSource] = useState(false);
  const [mergingDefault, setMergingDefault] = useState(false);
  const [adoptModalOpen, setAdoptModalOpen] = useState(false);
  const [syncing, setSyncing] = useState(false);

  // Manual scan trigger — fires the autonomous scan job NOW so the
  // operator doesn't have to wait for the 3-minute auto-tick.
  // Refreshes review comments + task status for THIS task (and
  // every other live task as a side effect — the underlying job
  // iterates all assigned + review tasks). Keeps the operator in
  // control of when provider APIs (Bitbucket / GitHub / GitLab)
  // get hit, instead of the old 30s firehose.
  async function onSyncNow() {
    if (syncing) { return; }
    setSyncing(true);
    try {
      const result = await triggerScan();
      if (result.ok) {
        toast.show({
          kind: 'success',
          title: 'Scan triggered',
          message: 'Kato is checking for new tasks, status changes, and review comments.',
        });
      } else {
        toast.show({
          kind: 'error',
          title: 'Scan failed',
          message: result.error || result.body?.error || 'unknown error',
        });
      }
    } finally {
      setSyncing(false);
    }
  }
  const pushApproval = usePushApproval(session?.task_id || '');
  const taskPublish = useTaskPublish(session?.task_id || '');
  if (!session) { return null; }
  const baseStatus = deriveTabStatus(session);
  const status = resolveTabStatus(session, needsAttention);
  const agentSessionId = session[AGENT_SESSION_ID] || '';
  const isLoading = baseStatus === TAB_STATUS.PROVISIONING;
  // Session is "resumable" when the streaming subprocess isn't
  // running — the operator stopped it, it ended on its own, or the
  // tab loaded against a record with no live process. In those
  // states the Stop button morphs into Resume so the operator has
  // an explicit way to respawn (instead of typing "please continue"
  // into the chat as a workaround).
  const isResumable = (
    streamLifecycle === SESSION_LIFECYCLE.CLOSED
    || streamLifecycle === SESSION_LIFECYCLE.IDLE
    || streamLifecycle === SESSION_LIFECYCLE.MISSING
  );

  async function onStop() {
    setStopping(true);
    const result = await postSession(session.task_id, 'stop');
    setStopping(false);
    if (typeof onStopped === 'function') {
      onStopped(result);
    }
  }

  async function onResumeClick() {
    if (resuming) { return; }
    if (typeof onResume !== 'function') { return; }
    setResuming(true);
    try {
      await onResume();
    } finally {
      setResuming(false);
    }
  }

  async function onPull() {
    if (taskPublish.pullBusy) { return; }
    const result = await taskPublish.pull();
    if (typeof taskPublish.refresh === 'function') {
      taskPublish.refresh();
    }
    const { title, message, kind } = formatPullResult(result);
    toast.show({
      kind,
      title,
      message,
      durationMs: kind === 'error' ? 12000 : 7000,
    });
  }

  // Fetch + merge the repo's default branch into the task branch
  // (the agent's clone can't run git itself). On conflict the
  // markers are left in the tree and we tell the chat agent —
  // listing the exact files — to resolve them.
  async function onMergeDefault() {
    if (mergingDefault) { return; }
    setMergingDefault(true);
    const result = await mergeDefaultBranch(session.task_id);
    setMergingDefault(false);
    if (typeof taskPublish.refresh === 'function') {
      taskPublish.refresh();
    }
    const body = result.body || {};
    if (!result.ok && !body.has_conflicts && !body.merged) {
      toast.show({
        kind: 'error',
        title: 'Merge failed',
        message: String(body.error || result.error || 'merge failed'),
        durationMs: 12000,
      });
      return;
    }
    const conflicted = Array.isArray(body.conflicted_repositories)
      ? body.conflicted_repositories : [];
    if (conflicted.length > 0) {
      const fileLines = conflicted.flatMap((repo) =>
        (repo.conflicted_files || []).map(
          (f) => `- ${repo.repository_id}: ${f}`,
        ),
      );
      const defaultBranch =
        conflicted[0]?.default_branch || 'the default branch';
      // Tell the agent to resolve — it can't run git, but it CAN
      // edit the conflicted files. kato's normal commit/push then
      // finalises the merge.
      const instruction =
        `I merged origin/${defaultBranch} into this task branch and `
        + `there are merge conflicts. The clone can't run git, so do `
        + `NOT try git commands — just edit these files to resolve `
        + `every conflict (remove all <<<<<<< / ======= / >>>>>>> `
        + `markers, keeping both sides' intent where it makes sense), `
        + `then continue:\n${fileLines.join('\n')}`;
      const sent = await postChatMessage(session.task_id, instruction);
      toast.show({
        kind: 'warning',
        title: `Merged ${defaultBranch} — conflicts to resolve`,
        message: sent && sent.ok
          ? `${fileLines.length} conflicted file(s). Asked Claude in the `
            + 'chat to resolve them.'
          : `${fileLines.length} conflicted file(s). Couldn't reach the `
            + 'chat — resolve manually or message Claude yourself.',
        durationMs: 12000,
      });
      return;
    }
    const mergedRepos = Array.isArray(body.merged_repositories)
      ? body.merged_repositories : [];
    if (mergedRepos.length > 0) {
      const total = mergedRepos.reduce(
        (n, r) => n + (Number(r.commits_merged) || 0), 0,
      );
      toast.show({
        kind: 'success',
        title: 'Default branch merged',
        message: `Clean merge into ${mergedRepos.length} repo(s) `
          + `(${total} commit(s)). No conflicts.`,
        durationMs: 7000,
      });
      return;
    }
    toast.show({
      kind: 'info',
      title: 'Nothing to merge',
      message: 'Task branch already contains the default branch '
        + '(or no repo was eligible).',
      durationMs: 6000,
    });
  }

  // Open the task's pull request(s) on the provider in new browser
  // tabs. Multi-repo tasks can have one PR per repo, so open them
  // all (the click is a direct user gesture, so the browser allows
  // the batch). ``noopener,noreferrer`` keeps the opened pages from
  // reaching back into the planning UI.
  function onOpenPullRequest() {
    const urls = Array.isArray(taskPublish.pullRequestUrls)
      ? taskPublish.pullRequestUrls.filter(Boolean) : [];
    if (urls.length === 0) { return; }
    urls.forEach((url) => {
      window.open(url, '_blank', 'noopener,noreferrer');
    });
  }

  async function onUpdateSource() {
    if (updatingSource) { return; }
    setUpdatingSource(true);
    const result = await updateTaskSource(session.task_id);
    setUpdatingSource(false);
    if (typeof taskPublish.refresh === 'function') {
      taskPublish.refresh();
    }
    const { title, message } = formatUpdateSourceResult(result);
    const body = (result && result.body) || {};
    const failed = (body.failed_repositories || []).length;
    const updated = (body.updated_repositories || []).length;
    const warnings = body.warnings || [];
    // Stash conflicts (or any warning) downgrade success → warning
    // so the toast is yellow, not green — operator should see it
    // and act on the conflict markers in the working tree.
    const hasWarnings = warnings.length > 0;
    let kind;
    if (!result.ok || failed > 0) {
      kind = updated > 0 ? 'warning' : 'error';
    } else if (hasWarnings) {
      kind = 'warning';
    } else {
      kind = 'success';
    }
    toast.show({
      kind,
      title,
      message,
      durationMs: kind === 'error' ? 12000 : 8000,
    });
  }

  async function onFinish() {
    if (finishing) { return; }
    setFinishing(true);
    const result = await finishTask(session.task_id);
    setFinishing(false);
    // Force a publish-state refresh so the Push/PR buttons reflect
    // the new state immediately (PR exists, nothing to push).
    if (typeof taskPublish.refresh === 'function') {
      taskPublish.refresh();
    }
    // Toast classification: full success → green, partial → amber,
    // request-level failure → red. Multi-line message is fine — the
    // toast component renders <pre> and wraps long lines.
    const { title, message } = formatFinishResult(result, session.task_id);
    const body = (result && result.body) || {};
    const kind = !result.ok
      ? 'error'
      : body.finished
        ? 'success'
        : 'warning';
    toast.show({
      kind,
      title,
      message,
      durationMs: kind === 'error' ? 12000 : 7000,
    });
  }

  const idleAlive = status === TAB_STATUS.ACTIVE
    && !turnInFlight
    && session?.working === false;
  const dotClass = [
    'status-dot',
    `status-${status}`,
    isLoading ? 'is-loading' : '',
    idleAlive ? 'is-idle-alive' : '',
  ].filter(Boolean).join(' ');
  const stopLabel = stopping ? 'Stopping…' : 'Stop';
  const resumeLabel = resuming ? 'Resuming…' : 'Resume';
  const pushLabel = pushApproval.busy ? 'Pushing…' : 'Approve push';
  const approvePushButton = pushApproval.awaiting && (
    <button
      id="session-approve-push"
      type="button"
      className="session-action tooltip-below"
      data-tooltip="Approve push: kato will push the branch and open the pull request."
      onClick={pushApproval.approve}
      disabled={pushApproval.busy}
      aria-label={pushLabel}
    >
      <Icon name={pushApproval.busy ? 'spinner' : 'check'} spin={pushApproval.busy} />
    </button>
  );

  const claudeStatus = describeClaudeStatus(
    streamLifecycle,
    turnInFlight,
    baseStatus,
    needsAttention,
  );
  // The Push button is *only* gated on "is there anything to push?" —
  // not on workspace existence, not on PR existence. When everything's
  // already on the remote we disable it (clicking would be a no-op);
  // otherwise it's clickable and worst case the click surfaces an
  // error the operator can act on.
  const pushDisabled = !taskPublish.hasChangesToPush || taskPublish.pushBusy;
  const pushTitle = pushTitleFor(taskPublish);
  // Pull is enabled whenever there's a workspace to pull into. We
  // can't cheaply pre-check "is the remote ahead?" without a fetch
  // (and operators would then complain the button is mysteriously
  // disabled), so we let the click run; the toast surfaces the
  // outcome — already in sync, dirty tree refusal, or
  // commits-pulled count.
  const pullDisabled = !taskPublish.hasWorkspace || taskPublish.pullBusy;
  const pullTitle = pullTitleFor(taskPublish);
  const prDisabled = !taskPublish.hasWorkspace
    || taskPublish.hasPullRequest
    || taskPublish.prBusy;
  const prTitle = prTitleFor(taskPublish);
  const openPrUrls = Array.isArray(taskPublish.pullRequestUrls)
    ? taskPublish.pullRequestUrls.filter(Boolean) : [];
  const openPrDisabled = openPrUrls.length === 0;
  let openPrTitle;
  if (openPrDisabled) {
    openPrTitle = 'No pull request yet — open one with the adjacent '
      + 'Pull request button (or Done) first.';
  } else if (openPrUrls.length === 1) {
    openPrTitle = 'Open the pull request on the provider in a new '
      + 'browser tab.';
  } else {
    openPrTitle = `Open all ${openPrUrls.length} pull requests `
      + '(one per repository) in new browser tabs.';
  }
  // Per AGENTS.md "no logic inside JSX": every label / element /
  // condition that the return statement consumes is precomputed
  // here so the JSX below is pure rendering.
  const taskSummary = session.task_summary || '';
  const sessionIdBadge = agentSessionId ? (
    <span
      id="session-claude-id"
      className="claude-session-id"
      title={
        `Agent session id: ${agentSessionId}\n`
        + 'kato resumes this id across restarts — compare it '
        + 'before/after a restart to confirm the conversation '
        + 'was continued, not started fresh.'
      }
    >
      sid:{agentSessionId.slice(0, 8)}…
    </span>
  ) : null;
  const pushButtonLabel = taskPublish.pushBusy ? 'Pushing…' : 'Push';
  const pullButtonLabel = taskPublish.pullBusy ? 'Pulling…' : 'Pull';
  const prButtonLabel = taskPublish.prBusy ? 'Opening PR…' : 'Pull request';
  const updateSourceDisabled = updatingSource || !taskPublish.hasWorkspace;
  const updateSourceTitle = !taskPublish.hasWorkspace
    ? 'No workspace for this task — workspace must be provisioned before source can be updated.'
    : 'Update source — push the task branch, then for each repo under REPOSITORY_ROOT_PATH: fetch, checkout the task branch, and pull. Lets you test the task on your live running system. Refuses if a source repo has uncommitted changes.';
  const updateSourceLabel = updatingSource ? 'Updating source…' : 'Update source';
  const finishLabel = finishing ? 'Finishing…' : 'Done';
  const stopOrResumeButton = isResumable ? (
    <button
      id="session-resume"
      type="button"
      className="session-action tooltip-below is-warning"
      data-tooltip="Resume the Claude session — kato will respawn the subprocess and ask Claude to pick up where it left off."
      onClick={onResumeClick}
      disabled={resuming || typeof onResume !== 'function'}
      aria-label={resumeLabel}
    >
      <Icon name={resuming ? 'spinner' : 'play'} spin={resuming} />
    </button>
  ) : (
    <button
      id="session-stop"
      type="button"
      className="session-action tooltip-below is-danger"
      data-tooltip="Stop the live Claude subprocess for this task. The chat history is preserved; you can resume from this header when the subprocess has ended."
      onClick={onStop}
      // Enabled whenever this Stop variant is rendered. The
      // ``isResumable`` branch above already swapped to Resume when
      // the subprocess isn't live — so if we're rendering Stop, the
      // subprocess IS alive and stoppable. The previous
      // ``baseStatus !== ACTIVE`` guard silently DISABLED Stop while
      // Claude was WORKING (the exact moment operators want to use
      // it) because ``deriveTabStatus`` flips to ``WORKING`` while
      // ``session.working === true`` — the bug the operator
      // reported as "stop button doesn't stop the work".
      disabled={stopping}
      aria-label={stopLabel}
    >
      <Icon name={stopping ? 'spinner' : 'stop'} spin={stopping} />
    </button>
  );
  const adoptModal = adoptModalOpen ? (
    <AdoptSessionModal
      taskId={session.task_id}
      onClose={() => setAdoptModalOpen(false)}
      onAdopted={(adopted) => {
        setAdoptModalOpen(false);
        if (typeof onSessionAdopted === 'function') {
          onSessionAdopted(adopted);
        }
      }}
    />
  ) : null;

  return (
    <>
      <header id="session-header">
        <div className="session-header-info">
          <span
            id="session-status-dot"
            className={dotClass}
            title={tabStatusTitle(baseStatus, needsAttention)}
          />
          <strong id="session-task-id">{session.task_id}</strong>
          {sessionIdBadge}
          <span id="session-task-summary">{taskSummary}</span>
        </div>
        <div className="session-header-actions">
          <span
            id="session-claude-status"
            className={`claude-status claude-status-${claudeStatus.kind}`}
            title={claudeStatus.title}
          >
            Claude: {claudeStatus.label}
          </span>
          {searchSlot}
          {approvePushButton}
          <button
            id="session-push"
            type="button"
            className="session-action tooltip-below"
            data-tooltip={pushTitle}
            onClick={taskPublish.push}
            disabled={pushDisabled}
            aria-label={pushButtonLabel}
          >
            <Icon name={taskPublish.pushBusy ? 'spinner' : 'arrow-up'} spin={taskPublish.pushBusy} />
          </button>
          <button
            id="session-merge-default"
            type="button"
            className="session-action tooltip-below"
            data-tooltip="Merge the default branch (master/main) into this task branch. The agent's clone can't run git, so use this when the branch fell behind — on conflict the markers are left in place and Claude is told (with the file list) to resolve them."
            onClick={onMergeDefault}
            disabled={mergingDefault || !taskPublish.hasWorkspace}
            aria-label={mergingDefault ? 'Merging…' : 'Merge default branch'}
          >
            <Icon name={mergingDefault ? 'spinner' : 'merge'} spin={mergingDefault} />
          </button>
          <button
            id="session-pull"
            type="button"
            className="session-action tooltip-below"
            data-tooltip={pullTitle}
            onClick={onPull}
            disabled={pullDisabled}
            aria-label={pullButtonLabel}
          >
            <Icon name={taskPublish.pullBusy ? 'spinner' : 'arrow-down'} spin={taskPublish.pullBusy} />
          </button>
          <button
            id="session-pull-request"
            type="button"
            className="session-action tooltip-below"
            data-tooltip={prTitle}
            onClick={taskPublish.createPullRequest}
            disabled={prDisabled}
            aria-label={prButtonLabel}
          >
            <Icon name={taskPublish.prBusy ? 'spinner' : 'pull-request'} spin={taskPublish.prBusy} />
          </button>
          <button
            id="session-open-pull-request"
            type="button"
            className="session-action tooltip-below"
            data-tooltip={openPrTitle}
            onClick={onOpenPullRequest}
            disabled={openPrDisabled}
            aria-label="Open pull request in a new tab"
          >
            <Icon name="external-link" />
          </button>
          <button
            id="session-update-source"
            type="button"
            className="session-action tooltip-below"
            data-tooltip={updateSourceTitle}
            onClick={onUpdateSource}
            disabled={updateSourceDisabled}
            aria-label={updateSourceLabel}
          >
            <Icon name={updatingSource ? 'spinner' : 'refresh'} spin={updatingSource} />
          </button>
          <button
            id="session-finish"
            type="button"
            className="session-action tooltip-below is-primary"
            data-tooltip="Done — push pending changes, open a PR if missing, and move the ticket to In Review. Same flow Claude can trigger by emitting <KATO_TASK_DONE>."
            onClick={onFinish}
            disabled={finishing}
            aria-label={finishLabel}
          >
            <Icon name={finishing ? 'spinner' : 'check'} spin={finishing} />
          </button>
          <button
            id="session-sync"
            type="button"
            className="session-action tooltip-below"
            data-tooltip="Sync now — run a scan immediately to pick up new review comments, status changes, and PR updates without waiting for the next 3-minute auto-tick."
            onClick={onSyncNow}
            disabled={syncing}
            aria-label={syncing ? 'Syncing…' : 'Sync now'}
          >
            <Icon name={syncing ? 'spinner' : 'history'} spin={syncing} />
          </button>
          <button
            id="session-adopt-claude"
            type="button"
            className="session-action tooltip-below"
            data-tooltip="Adopt an existing Claude Code session for this task — e.g. a chat you already started in the VS Code extension. Kato will --resume that session on the next agent spawn instead of starting fresh."
            onClick={() => setAdoptModalOpen(true)}
            aria-label="Adopt session"
          >
            <Icon name="link" />
          </button>
          {stopOrResumeButton}
        </div>
      </header>
      {adoptModal}
    </>
  );
}

// Persistent header shown when NO task is selected. The bar must
// never disappear (a header that hides/shows as you click around is
// jarring) — so we keep the exact same shell, show a "Select a task"
// title on the left, and render the full action row on the right but
// inert (disabled + not focusable). No layout jump when a task is
// then selected and the real SessionHeader takes over.
export function SessionHeaderPlaceholder() {
  const buttons = [
    { icon: 'search', label: 'Search' },
    { icon: 'arrow-up', label: 'Push' },
    { icon: 'merge', label: 'Merge default branch' },
    { icon: 'arrow-down', label: 'Pull' },
    { icon: 'pull-request', label: 'Open pull request' },
    { icon: 'external-link', label: 'Open pull request in a new tab' },
    { icon: 'refresh', label: 'Update source' },
    { icon: 'check', label: 'Finish', primary: true },
    { icon: 'history', label: 'Sync now' },
    { icon: 'link', label: 'Adopt session' },
    { icon: 'stop', label: 'Stop' },
  ];
  return (
    <header id="session-header" className="is-empty">
      <div className="session-header-info">
        <span id="session-status-dot" className="status-dot status-dot-idle" />
        <span id="session-task-summary" className="is-placeholder">
          Select a task
        </span>
      </div>
      <div className="session-header-actions" aria-hidden="true">
        <span
          id="session-claude-status"
          className="claude-status claude-status-idle"
        >
          Claude: no task
        </span>
        {buttons.map((b) => (
          <button
            key={b.icon}
            type="button"
            className={`session-action${b.primary ? ' is-primary' : ''}`}
            disabled
            tabIndex={-1}
            aria-label={b.label}
          >
            <Icon name={b.icon} />
          </button>
        ))}
      </div>
    </header>
  );
}

function pullTitleFor(state) {
  if (state.pullBusy) { return 'Pull in progress…'; }
  if (!state.hasWorkspace) {
    return 'Nothing to pull — kato has not provisioned a workspace for this task yet.';
  }
  return 'Fast-forward the workspace clone(s) from origin. Refuses if the working tree is dirty.';
}

function pushTitleFor(state) {
  if (state.pushBusy) { return 'Push in progress…'; }
  if (!state.hasWorkspace) {
    return 'Nothing to push — kato has not provisioned a workspace for this task yet.';
  }
  if (!state.hasChangesToPush) {
    return 'Nothing to push — every repository is already in sync with its remote.';
  }
  return 'Push the current branch to its remote (no PR opened).';
}

function prTitleFor(state) {
  if (!state.hasWorkspace) {
    return 'No workspace yet — kato needs to provision the task before you can open a PR.';
  }
  if (state.hasPullRequest) {
    const url = (state.pullRequestUrls && state.pullRequestUrls[0]) || '';
    return url
      ? `Pull request already exists: ${url}`
      : 'Pull request already exists for this task.';
  }
  if (state.prBusy) { return 'Opening pull request…'; }
  return 'Push the branch and open a pull request.';
}

// Map (lifecycle, turnInFlight) → a short status word + tooltip for the
// Claude agent indicator. ``streamLifecycle`` is undefined when the
// header is rendered without a stream context (defensive — should not
// happen in normal use but keeps the chip from blowing up).
function describeClaudeStatus(
  streamLifecycle,
  turnInFlight,
  baseStatus,
  needsAttention,
) {
  if (baseStatus === TAB_STATUS.PROVISIONING) {
    return {
      kind: 'provisioning',
      label: 'provisioning',
      title: 'Workspace is being set up.',
    };
  }
  if (turnInFlight) {
    return {
      kind: 'working',
      label: 'working',
      title: 'Claude is processing the current turn.',
    };
  }
  if (needsAttention) {
    return {
      kind: 'approval',
      label: 'approval',
      title: 'Claude is paused waiting for your approval.',
    };
  }
  switch (streamLifecycle) {
    case SESSION_LIFECYCLE.STREAMING:
      return {
        kind: 'idle',
        label: 'idle',
        title: 'Claude is connected and waiting for input.',
      };
    case SESSION_LIFECYCLE.CONNECTING:
      return {
        kind: 'connecting',
        label: 'connecting',
        title: 'Connecting to the Claude session…',
      };
    case SESSION_LIFECYCLE.IDLE:
      return {
        kind: 'sleeping',
        label: 'sleeping',
        title: 'No live subprocess — kato will respawn Claude on the next message.',
      };
    case SESSION_LIFECYCLE.CLOSED:
      return {
        kind: 'closed',
        label: 'closed',
        title: 'The Claude subprocess for this task has ended.',
      };
    case SESSION_LIFECYCLE.MISSING:
      return {
        kind: 'missing',
        label: 'no record',
        title: 'No record for this task on the server.',
      };
    default:
      return {
        kind: 'unknown',
        label: '—',
        title: 'Claude status unknown.',
      };
  }
}
