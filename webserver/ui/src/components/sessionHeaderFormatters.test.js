import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  formatFinishResult,
  formatPullResult,
  formatPushSummary,
  formatRequestFailure,
  formatUpdateSourceResult,
} from './sessionHeaderFormatters.js';

// Three previously-duplicated formatters now share building blocks
// here. The tests pin the bullet shape + classification rules so
// the next button to land can rely on them.

test('formatRequestFailure surfaces error string with the caller-supplied title', () => {
  const out = formatRequestFailure(
    { ok: false, error: 'connect timeout' },
    'Pull failed',
  );
  assert.equal(out.title, 'Pull failed');
  assert.equal(out.kind, 'error');
  assert.match(out.message, /connect timeout/);
});

test('formatRequestFailure falls back to body.error and finally to "unknown error"', () => {
  const withBodyError = formatRequestFailure(
    { ok: false, body: { error: 'rate limited' } },
    'X failed',
  );
  assert.match(withBodyError.message, /rate limited/);
  const noClue = formatRequestFailure({ ok: false }, 'X failed');
  assert.equal(noClue.message, 'unknown error');
});

test('formatPushSummary uses count_only mode for the update-source toast', () => {
  const out = formatPushSummary(
    { pushed_repositories: ['a', 'b'] },
    { pushedSummary: 'count_only' },
  );
  assert.equal(out, '✓ pushed 2 repo(s) to remote');
});

test('formatPushSummary uses with_ids mode for the finish toast', () => {
  const out = formatPushSummary(
    { pushed_repositories: ['client', 'server'] },
    { pushedSummary: 'with_ids' },
  );
  assert.equal(out, '✓ pushed 2 repo(s): client, server');
});

test('formatPushSummary surfaces failures with a "; "-joined detail string', () => {
  const out = formatPushSummary({
    failed_repositories: [
      { repository_id: 'a', error: 'auth' },
      { repository_id: 'b', error: 'net' },
    ],
  });
  assert.equal(out, '✗ push failed: a: auth; b: net');
});

test('formatPushSummary returns null when nothing happened', () => {
  assert.equal(formatPushSummary({}), null);
});

test('formatPullResult: success path lists per-repo commit counts and titles "Pulled"', () => {
  const out = formatPullResult({
    ok: true,
    body: {
      pulled_repositories: [
        { repository_id: 'client', commits_pulled: 3 },
        { repository_id: 'server', commits_pulled: 1 },
      ],
      skipped_repositories: [],
      failed_repositories: [],
    },
  });
  assert.equal(out.title, 'Pulled');
  assert.equal(out.kind, 'success');
  assert.match(out.message, /✓ client: pulled 3 commit\(s\)/);
  assert.match(out.message, /✓ server: pulled 1 commit\(s\)/);
});

test('formatPullResult: dirty-tree skip is a warning bullet, not a failure', () => {
  const out = formatPullResult({
    ok: true,
    body: {
      pulled_repositories: [],
      skipped_repositories: [{
        repository_id: 'client',
        reason: 'dirty_working_tree',
        detail: 'has uncommitted edits',
      }],
      failed_repositories: [],
    },
  });
  assert.equal(out.kind, 'warning');
  assert.match(out.message, /⚠ client: has uncommitted edits/);
});

test('formatPullResult: already-in-sync renders as "nothing to pull"', () => {
  const out = formatPullResult({
    ok: true,
    body: {
      pulled_repositories: [],
      skipped_repositories: [{ repository_id: 'client', reason: 'already_in_sync' }],
      failed_repositories: [],
    },
  });
  assert.match(out.message, /• client: nothing to pull/);
});

test('formatPullResult: any failure with no successes is an error toast', () => {
  const out = formatPullResult({
    ok: true,
    body: {
      pulled_repositories: [],
      skipped_repositories: [],
      failed_repositories: [{ repository_id: 'client', error: 'fetch refused' }],
    },
  });
  assert.equal(out.kind, 'error');
  assert.equal(out.title, 'Nothing to pull');
  assert.match(out.message, /✗ client: fetch refused/);
});

test('formatPullResult: partial success (some pulled + some failed) downgrades to warning', () => {
  const out = formatPullResult({
    ok: true,
    body: {
      pulled_repositories: [{ repository_id: 'client', commits_pulled: 2 }],
      skipped_repositories: [],
      failed_repositories: [{ repository_id: 'server', error: 'fetch refused' }],
    },
  });
  assert.equal(out.kind, 'warning');
  assert.equal(out.title, 'Pull partially completed');
});

test('formatPullResult: empty workspace shows the friendly placeholder', () => {
  const out = formatPullResult({ ok: true, body: {} });
  assert.match(out.message, /no repositories in workspace/);
});

test('formatUpdateSourceResult: pushed-and-updated shows both lines', () => {
  const out = formatUpdateSourceResult({
    ok: true,
    body: {
      updated: true,
      pushed: { pushed_repositories: ['client'] },
      updated_repositories: ['client'],
    },
  });
  assert.equal(out.title, 'Source updated');
  assert.match(out.message, /✓ pushed 1 repo\(s\) to remote/);
  assert.match(out.message, /✓ source updated for 1 repo\(s\): client/);
});

test('formatUpdateSourceResult: per-repo warnings get the right marker', () => {
  const out = formatUpdateSourceResult({
    ok: true,
    body: {
      updated: true,
      pushed: {},
      updated_repositories: ['client'],
      warnings: [
        { warning: 'stash reapplied with conflicts', stash_conflict: true },
        { warning: 'note something else', stash_conflict: false },
      ],
    },
  });
  assert.match(out.message, /⚠ stash reapplied with conflicts/);
  assert.match(out.message, /• note something else/);
});

test('formatFinishResult: full happy path includes push, PR, and move-to-review lines', () => {
  const out = formatFinishResult({
    ok: true,
    body: {
      finished: true,
      pushed: { pushed_repositories: ['client'] },
      pull_request: {
        created_pull_requests: [{ url: 'https://example/pr/1' }],
      },
      moved_to_review: true,
    },
  });
  assert.equal(out.title, 'Done — task finalised');
  assert.match(out.message, /✓ pushed 1 repo\(s\): client/);
  assert.match(out.message, /✓ opened 1 pull request\(s\): https:\/\/example\/pr\/1/);
  assert.match(out.message, /✓ ticket moved to In Review/);
});

test('formatFinishResult: title includes task id when supplied', () => {
  // The toast title used to be just "Done — task finalised" — when an
  // operator had several tabs mid-flow it was easy to lose track of
  // which task the toast was for. Passing ``taskId`` interpolates it
  // into the title.
  const out = formatFinishResult(
    {
      ok: true,
      body: {
        finished: true,
        pushed: { pushed_repositories: ['client'] },
        pull_request: { skipped_existing: ['client'] },
        moved_to_review: true,
      },
    },
    'UNA-2536',
  );
  assert.equal(out.title, 'Done — task finalised (UNA-2536)');
});

test('formatFinishResult: omitting task id keeps the bare title', () => {
  const out = formatFinishResult({
    ok: true,
    body: {
      finished: true,
      pushed: { pushed_repositories: [] },
      pull_request: {},
      moved_to_review: true,
    },
  });
  assert.equal(out.title, 'Done — task finalised');
});

test('formatFinishResult: missing move-to-review surfaces the reason', () => {
  const out = formatFinishResult({
    ok: true,
    body: {
      finished: false,
      pushed: { pushed_repositories: [] },
      pull_request: { skipped_existing: ['client'] },
      moved_to_review: false,
      move_error: 'state field locked',
    },
  });
  assert.equal(out.title, 'Done — partial completion');
  assert.match(out.message, /✗ ticket did NOT move to In Review: state field locked/);
});
