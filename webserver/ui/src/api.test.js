import assert from 'node:assert/strict';
import test, { afterEach } from 'node:test';

import {
  adoptAgentSession,
  fetchBaseFileContent,
  fetchClaudeSessions,
  postChatMessage,
} from './api.js';
import { AGENT_SESSION_ID } from './constants/sessionFields.js';


function _stubFetch(response) {
  const calls = [];
  globalThis.fetch = function (url, init) {
    calls.push({ url, init });
    return Promise.resolve(response);
  };
  return calls;
}

function _stubFetchResponses(responses) {
  const calls = [];
  globalThis.fetch = function (url, init) {
    calls.push({ url, init });
    const response = responses.shift();
    return Promise.resolve(response);
  };
  return calls;
}

afterEach(function () {
  delete globalThis.fetch;
});


test('fetchClaudeSessions hits /api/claude/sessions with no query when empty', async function () {
  const calls = _stubFetch({
    ok: true,
    json: () => Promise.resolve({ sessions: [] }),
  });
  await fetchClaudeSessions('');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/claude/sessions');
});

test('fetchClaudeSessions URL-encodes the query string', async function () {
  const calls = _stubFetch({
    ok: true,
    json: () => Promise.resolve({ sessions: [] }),
  });
  await fetchClaudeSessions('auth flow');
  assert.equal(calls[0].url, '/api/claude/sessions?q=auth%20flow');
});

test('fetchClaudeSessions throws when the response is not ok', async function () {
  _stubFetch({
    ok: false,
    status: 500,
    statusText: 'Server Error',
    json: () => Promise.resolve({ error: 'storage corrupt' }),
  });
  await assert.rejects(
    () => fetchClaudeSessions(''),
    /storage corrupt/,
  );
});

test('fetchBaseFileContent falls back to the current file when base route 404s', async function () {
  const calls = _stubFetchResponses([
    {
      ok: false,
      status: 404,
      statusText: 'NOT FOUND',
      json: () => Promise.resolve({ error: 'file not found at base' }),
    },
    {
      ok: true,
      status: 200,
      json: () => Promise.resolve({ content: 'current text', binary: false }),
    },
  ]);
  const result = await fetchBaseFileContent('TASK-1', {
    repoId: 'client',
    repoCwd: '/workspace/client',
    path: 'src/app.js',
  });
  assert.equal(result.content, 'current text');
  assert.equal(
    calls[0].url,
    '/api/sessions/TASK-1/base-file?path=src%2Fapp.js&repo=client',
  );
  assert.equal(
    calls[1].url,
    '/api/sessions/TASK-1/file?path=%2Fworkspace%2Fclient%2Fsrc%2Fapp.js',
  );
});

test('fetchBaseFileContent keeps the base-file error when fallback is unavailable', async function () {
  _stubFetch({
    ok: false,
    status: 404,
    statusText: 'NOT FOUND',
    json: () => Promise.resolve({ error: 'file not found at base' }),
  });
  await assert.rejects(
    () => fetchBaseFileContent('TASK-1', { repoId: 'client', path: 'src/app.js' }),
    /file not found at base/,
  );
});

test('adoptAgentSession posts the session id as JSON', async function () {
  const calls = _stubFetch({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ task_id: 'PROJ-1', [AGENT_SESSION_ID]: 'sess-1' }),
  });
  const result = await adoptAgentSession('PROJ-1', 'sess-1');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/sessions/PROJ-1/adopt-agent-session');
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.headers['content-type'], 'application/json');
  assert.deepEqual(JSON.parse(calls[0].init.body), { [AGENT_SESSION_ID]: 'sess-1' });
  assert.equal(result.ok, true);
  assert.equal(result.body[AGENT_SESSION_ID], 'sess-1');
});

test('adoptAgentSession returns ok=false without calling fetch when task_id is empty', async function () {
  const calls = _stubFetch({
    ok: true,
    json: () => Promise.resolve({}),
  });
  const result = await adoptAgentSession('', 'sess-1');
  assert.equal(result.ok, false);
  assert.equal(calls.length, 0);
});

test('adoptAgentSession returns ok=false without calling fetch when session id is empty', async function () {
  const calls = _stubFetch({
    ok: true,
    json: () => Promise.resolve({}),
  });
  const result = await adoptAgentSession('PROJ-1', '');
  assert.equal(result.ok, false);
  assert.equal(calls.length, 0);
});

test('adoptAgentSession surfaces backend error body when status is non-2xx', async function () {
  _stubFetch({
    ok: false,
    status: 409,
    json: () => Promise.resolve({ error: 'live session running' }),
  });
  const result = await adoptAgentSession('PROJ-1', 'sess-1');
  assert.equal(result.ok, false);
  assert.equal(result.status, 409);
  assert.equal(result.body.error, 'live session running');
});

test('adoptAgentSession URL-encodes the task id', async function () {
  const calls = _stubFetch({
    ok: true,
    status: 200,
    json: () => Promise.resolve({}),
  });
  await adoptAgentSession('PROJ/1', 'sess-1');
  assert.equal(calls[0].url, '/api/sessions/PROJ%2F1/adopt-agent-session');
});


test('postChatMessage sends text and images JSON to /messages', async function () {
  const calls = _stubFetch({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ status: 'delivered', image_count: 1 }),
  });
  const result = await postChatMessage('PROJ-1', 'look at this', [
    { media_type: 'image/png', data: 'AAAA' },
  ]);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/sessions/PROJ-1/messages');
  assert.equal(calls[0].init.method, 'POST');
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.text, 'look at this');
  assert.equal(body.images.length, 1);
  assert.equal(body.images[0].media_type, 'image/png');
  assert.equal(result.ok, true);
});

test('postChatMessage with no images sends an empty images array', async function () {
  const calls = _stubFetch({
    ok: true,
    status: 200,
    json: () => Promise.resolve({}),
  });
  await postChatMessage('PROJ-1', 'hi');
  const body = JSON.parse(calls[0].init.body);
  assert.deepEqual(body.images, []);
});

test('postChatMessage surfaces backend errors', async function () {
  _stubFetch({
    ok: false,
    status: 400,
    statusText: 'Bad Request',
    json: () => Promise.resolve({ error: 'text or images is required' }),
  });
  const result = await postChatMessage('PROJ-1', '', []);
  assert.equal(result.ok, false);
  assert.equal(result.status, 400);
  assert.match(result.error, /required/);
});

test('postChatMessage refuses without a task id', async function () {
  const calls = _stubFetch({ ok: true, json: () => Promise.resolve({}) });
  const result = await postChatMessage('', 'hi', []);
  assert.equal(result.ok, false);
  assert.equal(calls.length, 0);
});
