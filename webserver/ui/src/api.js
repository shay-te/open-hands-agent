// Single fetch surface for the planning UI.
//
// Two flavors:
//   * `fetchJson(url)`   — GETs that return JSON; throws Error(server-message)
//                          on non-2xx so UIs can render a clean line.
//   * `postSession(taskId, endpoint, body?)` — per-task POSTs that return a
//                          uniform `{ ok, status, error }` so the UI can
//                          branch declaratively without try/catch sprawl.

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

export function fetchFileTree(taskId) {
  return fetchJson(`/api/sessions/${encodeURIComponent(taskId)}/files`);
}

export function fetchDiff(taskId) {
  return fetchJson(`/api/sessions/${encodeURIComponent(taskId)}/diff`);
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
    return { ok: true, status: response.status };
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
