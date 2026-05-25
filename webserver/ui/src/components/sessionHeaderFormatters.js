// Toast-message formatters for the session-header action buttons.
//
// Pulled out of SessionHeader.jsx because three buttons (Push,
// Pull, Update source, Done) each need to translate a backend
// payload of the shape ``{pushed_repositories, skipped_repositories,
// failed_repositories}`` into a per-repo bullet list. Before this
// extraction the three formatters had drifted slightly — same
// shape, three different join orders, three different "nothing
// happened" stubs — and the next button to land would have made it
// four. One module, one set of building blocks.
//
// Pure functions only, no React. Component code stays a render-only
// JSX file (see AGENTS.md "no logic inside JSX").

// Render the "✓ pushed N repo(s) / • push skipped / ✗ push failed"
// line that both the ``Done`` and ``Update source`` toasts emit
// for the push step. The shape of ``pushed`` is the same payload
// kato's ``push_task`` returns.
export function formatPushSummary(pushed, options = {}) {
  const { pushedSummary } = options;
  const pushedRepositories = pushed.pushed_repositories || [];
  const skippedRepositories = pushed.skipped_repositories || [];
  const failedRepositories = pushed.failed_repositories || [];
  if (pushedRepositories.length) {
    if (pushedSummary === 'count_only') {
      return `✓ pushed ${pushedRepositories.length} repo(s) to remote`;
    }
    return `✓ pushed ${pushedRepositories.length} repo(s): ${pushedRepositories.join(', ')}`;
  }
  if (skippedRepositories.length) {
    return `• push skipped — already in sync (${skippedRepositories.length} repo(s))`;
  }
  if (failedRepositories.length) {
    const errs = failedRepositories
      .map((r) => `${r.repository_id}: ${r.error}`).join('; ');
    return `✗ push failed: ${errs}`;
  }
  return null;
}

// Format an arbitrary list of per-repo failure entries — used by
// both the pull and update-source flows. Each entry is
// ``{repository_id, error}``. Returns one bullet per entry.
export function formatFailedLines(failed) {
  return (failed || []).map((entry) => `✗ ${entry.repository_id}: ${entry.error}`);
}

// Standard "request-level failure" toast (the fetch itself bombed
// or the server returned !ok). Used as the bail-out branch for
// every formatter so they share one place to surface
// transport-level errors.
export function formatRequestFailure(result, fallbackTitle) {
  const body = (result && result.body) || {};
  return {
    title: fallbackTitle,
    kind: 'error',
    message: (result && result.error)
      || body.error
      || 'unknown error',
  };
}

// Build the toast for ``POST /pull``. Mirrors the shape kato's
// ``pull_task`` returns: per-repo pulled / skipped / failed lists.
export function formatPullResult(result) {
  if (!result || !result.ok) {
    return formatRequestFailure(result, 'Pull failed');
  }
  const body = result.body || {};
  const pulled = body.pulled_repositories || [];
  const skipped = body.skipped_repositories || [];
  const failed = body.failed_repositories || [];
  const lines = [];
  for (const entry of pulled) {
    const count = Number(entry.commits_pulled || 0);
    lines.push(`✓ ${entry.repository_id}: pulled ${count} commit(s)`);
  }
  for (const entry of skipped) {
    lines.push(formatPullSkipLine(entry));
  }
  lines.push(...formatFailedLines(failed));
  if (lines.length === 0) {
    lines.push('• no repositories in workspace');
  }
  return {
    title: pulled.length
      ? (failed.length ? 'Pull partially completed' : 'Pulled')
      : 'Nothing to pull',
    kind: classifyPullKind({ pulled, skipped, failed }),
    message: lines.join('\n'),
  };
}

function formatPullSkipLine(entry) {
  const reason = entry.reason || 'no_change';
  const detail = entry.detail || '';
  if (reason === 'already_in_sync' || reason === 'remote_branch_missing') {
    return `• ${entry.repository_id}: nothing to pull`;
  }
  if (reason === 'dirty_working_tree') {
    return `⚠ ${entry.repository_id}: ${detail || 'dirty working tree'}`;
  }
  return `• ${entry.repository_id}: ${detail || reason}`;
}

function classifyPullKind({ pulled, skipped, failed }) {
  if (failed.length > 0) {
    return pulled.length > 0 ? 'warning' : 'error';
  }
  if (skipped.some((entry) => entry.reason === 'dirty_working_tree')) {
    return 'warning';
  }
  return 'success';
}

// Build the toast for ``POST /update-source``. The shape mixes a
// nested ``pushed`` block (handled by ``formatPushSummary``) with
// per-repo update / warning / skip / fail lists.
export function formatUpdateSourceResult(result) {
  if (!result || !result.ok) {
    return formatRequestFailure(result, 'Update source failed');
  }
  const body = result.body || {};
  const lines = [];
  const pushLine = formatPushSummary(body.pushed || {}, { pushedSummary: 'count_only' });
  if (pushLine) { lines.push(pushLine); }
  const updated = body.updated_repositories || [];
  if (updated.length) {
    lines.push(`✓ source updated for ${updated.length} repo(s): ${updated.join(', ')}`);
  }
  for (const entry of (body.warnings || [])) {
    const text = String(entry.warning || '').trim();
    if (text) {
      lines.push(`${entry.stash_conflict ? '⚠' : '•'} ${text}`);
    }
  }
  for (const entry of (body.skipped_repositories || [])) {
    lines.push(`• skipped ${entry.repository_id}: ${entry.reason}`);
  }
  lines.push(...formatFailedLines(body.failed_repositories || []));
  if (lines.length === 0
      || (!updated.length && !(body.failed_repositories || []).length
          && !(body.skipped_repositories || []).length)) {
    if (!updated.length && !(body.failed_repositories || []).length
        && !(body.skipped_repositories || []).length) {
      lines.push('• no source repositories updated');
    }
  }
  return {
    title: body.updated
      ? ((body.failed_repositories || []).length ? 'Source partially updated' : 'Source updated')
      : 'Source not updated',
    message: lines.join('\n'),
  };
}

// Build the toast for ``POST /finish``. Three steps (push, PR,
// move-to-review) each get a single line with the failure reason
// inline when something didn't run.
//
// ``taskId`` (optional) is interpolated into the title so the toast
// makes it obvious WHICH task finished — without it the operator
// gets a generic "Done — task finalised" with no anchor, easy to
// confuse when several tabs are mid-flow.
export function formatFinishResult(result, taskId = '') {
  if (!result || !result.ok) {
    return formatRequestFailure(result, 'Finish request failed');
  }
  const body = result.body || {};
  const lines = [];
  const pushLine = formatPushSummary(body.pushed || {}, { pushedSummary: 'with_ids' });
  lines.push(pushLine || `• push: ${body.pushed?.error || 'no action'}`);
  lines.push(formatPullRequestStepLine(body.pull_request || {}));
  if (body.moved_to_review) {
    lines.push('✓ ticket moved to In Review');
  } else {
    lines.push(`✗ ticket did NOT move to In Review: ${body.move_error || 'unknown reason — check kato logs'}`);
  }
  const baseTitle = body.finished ? 'Done — task finalised' : 'Done — partial completion';
  const trimmedTask = String(taskId || '').trim();
  return {
    title: trimmedTask ? `${baseTitle} (${trimmedTask})` : baseTitle,
    message: lines.join('\n'),
  };
}

function formatPullRequestStepLine(pr) {
  const created = pr.created_pull_requests || [];
  const skipped = pr.skipped_existing || [];
  const failed = pr.failed_repositories || [];
  if (created.length) {
    const urls = created.map((r) => r.url || r.repository_id).join(', ');
    return `✓ opened ${created.length} pull request(s): ${urls}`;
  }
  if (skipped.length) {
    return `• PR skipped — already exists for ${skipped.length} repo(s)`;
  }
  if (failed.length) {
    const errs = failed.map((r) => `${r.repository_id}: ${r.error}`).join('; ');
    return `✗ PR failed: ${errs}`;
  }
  return `• pull request: ${pr.error || 'no action'}`;
}
