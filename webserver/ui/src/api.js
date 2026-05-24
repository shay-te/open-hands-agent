import { AGENT_SESSION_ID } from './constants/sessionFields.js';

async function fetchJson(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body && body.error) { message = body.error; }
    } catch (_) { /* fall through with status text */ }
    throw new Error(message);
  }
  return response.json();
}

export function fetchSessionList() {
  return fetchJson('/api/sessions');
}

export function fetchSafetyState() {
  return fetchJson('/api/safety');
}

// Settings drawer — currently exposes ``repository_root_path`` only.
// The shape ``{ ok, body }`` matches what fetchTaskComments returns
// so the drawer doesn't need a special-cased fetch wrapper.
export async function fetchSettings() {
  try {
    const response = await fetch('/api/settings');
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function updateSettings(payload) {
  try {
    const response = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// Repository approvals (used to live behind ``./kato approve-repo``).
export async function fetchRepositoryApprovals() {
  try {
    const response = await fetch('/api/repository-approvals');
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function updateRepositoryApprovals(payload) {
  try {
    const response = await fetch('/api/repository-approvals', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// Task providers — where tickets live + which kato polls
// (KATO_ISSUE_PLATFORM). Has an active selector.
export async function fetchTaskProviders() {
  try {
    const response = await fetch('/api/task-providers');
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function updateTaskProvider(payload) {
  try {
    const response = await fetch('/api/task-providers', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// Git hosts — credentials kato uses to clone / push / open PRs.
// NO active selector (host inferred from repo remote URLs).
export async function fetchGitProviders() {
  try {
    const response = await fetch('/api/git-providers');
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function updateGitProvider(payload) {
  try {
    const response = await fetch('/api/git-providers', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// Schema-driven "all settings" tabs (General, Claude agent, Sandbox,
// Security scanner, Email & Slack, OpenHands, Docker/infra, AWS).
// One GET returns the whole schema + resolved values; POST writes a
// {KEY: value} map (server-side whitelisted to the schema).
export async function fetchAllSettings() {
  try {
    const response = await fetch('/api/all-settings');
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function updateAllSettings(updates) {
  try {
    const response = await fetch('/api/all-settings', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ updates: updates || {} }),
    });
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export function fetchAwaitingPushApproval(taskId) {
  if (!taskId) {
    return Promise.resolve({ awaiting_push_approval: false });
  }
  return fetchJson(
    `/api/sessions/${encodeURIComponent(taskId)}/awaiting-push-approval`,
  );
}

export async function approveTaskPush(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/approve-push`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export function fetchTaskPublishState(taskId) {
  if (!taskId) {
    return Promise.resolve({
      has_workspace: false, has_pull_request: false,
    });
  }
  return fetchJson(
    `/api/sessions/${encodeURIComponent(taskId)}/publish-state`,
  );
}

export async function pushTask(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/push`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function pullTask(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/pull`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// Fetch + merge each clone's default branch into the task branch.
// A conflicted merge is a 200 with ``has_conflicts: true`` — the
// caller surfaces it + tells the chat agent to resolve the markers.
export async function mergeDefaultBranch(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/merge-default-branch`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function updateTaskSource(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/update-source`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// Diff-tab review comments: list / create / resolve / reopen /
// delete + sync from the source git platform.
export async function fetchTaskComments(taskId, repoId = '') {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  const params = repoId ? `?repo=${encodeURIComponent(repoId)}` : '';
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/comments${params}`,
    );
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      return { ok: false, status: response.status, error: body.error || response.statusText };
    }
    return { ok: true, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function createTaskComment(taskId, comment) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/comments`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(comment || {}),
      },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function resolveTaskComment(taskId, commentId) {
  if (!taskId || !commentId) { return { ok: false, error: 'no ids' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/comments/${encodeURIComponent(commentId)}/resolve`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function markTaskCommentAddressed(taskId, commentId, addressedSha = '') {
  if (!taskId || !commentId) { return { ok: false, error: 'no ids' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/comments/${encodeURIComponent(commentId)}/addressed`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ addressed_sha: addressedSha }),
      },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function reopenTaskComment(taskId, commentId) {
  if (!taskId || !commentId) { return { ok: false, error: 'no ids' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/comments/${encodeURIComponent(commentId)}/reopen`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function deleteTaskComment(taskId, commentId) {
  if (!taskId || !commentId) { return { ok: false, error: 'no ids' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/comments/${encodeURIComponent(commentId)}`,
      { method: 'DELETE' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function syncTaskComments(taskId, repoId) {
  if (!taskId || !repoId) { return { ok: false, error: 'no ids' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/comments/sync`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ repo: repoId }),
      },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}


// Every task assigned to the configured kato user — open, in
// progress, in review, done. Drives the left-panel "+ Add task"
// picker.
//
// We bound the wait with an AbortController. The endpoint
// synchronously calls into YouTrack / Jira; if the ticket platform
// is slow, rate-limited, or down, the modal would otherwise sit
// on "Loading tasks…" indefinitely. After the timeout we surface
// a short, operator-actionable error instead.
export async function fetchAllAssignedTasks({ timeoutMs = 30_000 } = {}) {
  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timeoutHandle = controller && typeof window !== 'undefined'
    ? window.setTimeout(() => controller.abort(), timeoutMs)
    : null;
  try {
    const response = await fetch(
      '/api/tasks',
      controller ? { signal: controller.signal } : undefined,
    );
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      return { ok: false, status: response.status, error: body.error || response.statusText };
    }
    return { ok: true, body };
  } catch (err) {
    if (err && err.name === 'AbortError') {
      return {
        ok: false,
        error: `ticket platform did not respond within ${Math.round(timeoutMs / 1000)}s `
             + '— check kato logs and the YouTrack/Jira connection',
      };
    }
    return { ok: false, error: String(err) };
  } finally {
    if (timeoutHandle !== null) { window.clearTimeout(timeoutHandle); }
  }
}

// Adopt an existing assigned task — provision the workspace + clone
// every repo the task touches. No agent spawn; operator drives that
// from the chat tab once the workspace lands.
export async function adoptTask(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/tasks/${encodeURIComponent(taskId)}/adopt`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// Recent commits on a repo's task branch (newest first). Drives the
// Files-tab per-repo "view commit" dropdown. ``limit`` is optional
// (server caps it at 200); ``repoId`` is required.
export async function fetchRepoCommits(taskId, repoId, { limit = 50 } = {}) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  if (!repoId) { return { ok: false, error: 'no repo id' }; }
  const params = new URLSearchParams({ repo: repoId, limit: String(limit) });
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/commits?${params}`,
    );
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      return { ok: false, status: response.status, error: body.error || response.statusText };
    }
    return { ok: true, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// Unified diff for a single commit on a repo. ``react-diff-view``'s
// parser eats the same shape ``/diff`` returns.
export async function fetchRepoCommitDiff(taskId, repoId, sha) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  if (!repoId) { return { ok: false, error: 'no repo id' }; }
  if (!sha) { return { ok: false, error: 'no sha' }; }
  const params = new URLSearchParams({ repo: repoId, sha });
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/commit?${params}`,
    );
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      return { ok: false, status: response.status, error: body.error || response.statusText };
    }
    return { ok: true, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}


// Add any task repositories missing from the workspace. Pure additive
// — repos already cloned, and repos no longer on the task, stay on
// disk untouched. The Files-tab sync icon calls this when the
// operator's added a ``kato:repo:<name>`` tag in YouTrack and wants
// kato to fetch the new repo without re-running the whole task.
export async function syncTaskRepositories(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/sync-repositories`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// List every repository in kato's inventory (the chooser source for
// "+ Add repository"). The picker filters out repos already on the
// task UI-side so the same payload can power other chooser UIs.
export async function fetchInventoryRepositories() {
  try {
    const response = await fetch('/api/repositories');
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      return { ok: false, error: body.error || response.statusText };
    }
    return { ok: true, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// Tag the task with ``kato:repo:<id>`` and clone the repo into the
// workspace. Atomic from the operator's perspective: one click,
// one toast, both halves done.
export async function addTaskRepository(taskId, repositoryId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  if (!repositoryId) { return { ok: false, error: 'no repository id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/add-repository`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ repository_id: repositoryId }),
      },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function finishTask(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/finish`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function createTaskPullRequest(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/pull-request`,
      { method: 'POST' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export function fetchModels() {
  return fetchJson('/api/models');
}

export function fetchSessionModel(taskId) {
  if (!taskId) { return Promise.resolve({ model: '' }); }
  return fetchJson(`/api/sessions/${encodeURIComponent(taskId)}/model`);
}

export async function setSessionModel(taskId, modelId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/model`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ model: modelId }),
      },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function triggerScan() {
  try {
    const response = await fetch('/api/scan/trigger', { method: 'POST' });
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function forgetTaskWorkspace(taskId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/workspace`,
      { method: 'DELETE' },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export function fetchFileTree(taskId) {
  return fetchJson(`/api/sessions/${encodeURIComponent(taskId)}/files`);
}

/**
 * Load a single tracked file's contents from the task workspace.
 * Server-side guards: path-traversal, 1MB cap, binary detection.
 * Returns ``{ ok, body }`` where body has either ``content`` (text),
 * ``binary: true`` (NUL bytes seen), or ``too_large: true``.
 */
export function fetchFileContent(taskId, absolutePath) {
  const url = `/api/sessions/${encodeURIComponent(taskId)}/file`
    + `?path=${encodeURIComponent(absolutePath)}`;
  return fetchJson(url);
}

export async function fetchBaseFileContent(
  taskId,
  { repoId = '', repoCwd = '', path = '' } = {},
) {
  const query = new URLSearchParams();
  query.set('path', path);
  if (repoId) { query.set('repo', repoId); }
  const url = `/api/sessions/${encodeURIComponent(taskId)}/base-file`;
  const response = await fetch(`${url}?${query.toString()}`, { cache: 'no-store' });
  if (response.ok) { return response.json(); }
  const body = await response.json().catch(() => ({}));
  if (response.status === 404 && repoCwd && path && path !== '/dev/null') {
    const absolutePath = path.startsWith('/')
      ? path
      : `${repoCwd.replace(/\/+$/, '')}/${path}`;
    return fetchFileContent(taskId, absolutePath);
  }
  throw new Error(body.error || `${response.status} ${response.statusText}`);
}

export function fetchDiff(taskId, { repoId = '' } = {}) {
  const url = `/api/sessions/${encodeURIComponent(taskId)}/diff`;
  const query = repoId ? `?repo_id=${encodeURIComponent(repoId)}` : '';
  return fetchJson(`${url}${query}`);
}

export function fetchClaudeSessions(query = '') {
  const qs = query ? `?q=${encodeURIComponent(query)}` : '';
  return fetchJson(`/api/claude/sessions${qs}`);
}

export async function adoptAgentSession(taskId, agentSessionId) {
  if (!taskId) { return { ok: false, error: 'no task id' }; }
  if (!agentSessionId) {
    return { ok: false, error: 'no agent session id' };
  }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/adopt-agent-session`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ [AGENT_SESSION_ID]: agentSessionId }),
      },
    );
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// Send a chat message with optional image attachments. The endpoint
// accepts the same shape as ``postSession(taskId, 'messages', {text})``
// but with an extra ``images`` array of ``{media_type, data}``
// entries. Kept separate from ``postSession`` so the call site reads
// "this is the message-with-attachments path" without having to
// know the body shape.
export async function postChatMessage(taskId, text, images = []) {
  if (!taskId) { return { ok: false, status: 0, error: 'no active task' }; }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/messages`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text, images }),
      },
    );
    let resultBody = null;
    try { resultBody = await response.json(); } catch (_) { /* ignore */ }
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: (resultBody && resultBody.error) || response.statusText,
      };
    }
    return { ok: true, status: response.status, body: resultBody };
  } catch (err) {
    return { ok: false, status: 0, error: String(err) };
  }
}

export async function postSession(taskId, endpoint, body) {
  if (!taskId) {
    return { ok: false, status: 0, error: 'no active task' };
  }
  const init = { method: 'POST' };
  if (body !== undefined) {
    init.headers = { 'content-type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  try {
    const response = await fetch(
      `/api/sessions/${encodeURIComponent(taskId)}/${endpoint}`,
      init,
    );
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: await safeReadError(response),
      };
    }
    let resultBody = null;
    try {
      resultBody = await response.json();
    } catch (_) { /* not all endpoints return json; that's fine */ }
    return { ok: true, status: response.status, body: resultBody };
  } catch (err) {
    return { ok: false, status: 0, error: String(err) };
  }
}

async function safeReadError(response) {
  try {
    const body = await response.json();
    return body.error || JSON.stringify(body);
  } catch (_) {
    return `${response.status} ${response.statusText}`;
  }
}
