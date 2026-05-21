// Backend ↔ UI contract test for /sync-repositories.
//
// Mirror of comments.contract.test.jsx: loads the captured JSON the
// real Flask backend produced (tests/test_sync_repositories_contract.py
// → __fixtures__/sync_repositories_contract.json) and asserts the
// keys the UI's toast renderer consumes are present.
//
// The UI's ``formatSyncResult`` reads the response to decide whether
// to show a red error, an amber partial, or a green success toast.
// The contract: the response always carries either ``error`` (string)
// or a ``synced`` boolean plus a ``task_id``.

import { describe, test, expect } from 'vitest';

import fixture from './__fixtures__/sync_repositories_contract.json';
import { formatSyncResult } from './FilesTab.jsx';

describe('/api/sessions/<task>/sync-repositories contract', () => {

  test('fixture has the three contract-relevant response shapes', () => {
    expect(fixture).toHaveProperty('no_workspace');
    expect(fixture).toHaveProperty('task_lookup_failed');
    expect(fixture).toHaveProperty('nothing_to_sync');
  });

  test('no-workspace response has synced=false + error string', () => {
    const r = fixture.no_workspace;
    expect(r.synced).toBe(false);
    expect(typeof r.error).toBe('string');
    expect(r.error).toMatch(/no workspace/);
    expect(r.task_id).toBeTruthy();
  });

  test('task-lookup-failed response carries an error the UI can show', () => {
    const r = fixture.task_lookup_failed;
    expect(r.synced).toBe(false);
    expect(typeof r.error).toBe('string');
    expect(r.error.length).toBeGreaterThan(0);
  });

  test('nothing-to-sync response has the keys the toast renderer reads', () => {
    const r = fixture.nothing_to_sync;
    // task_id is mandatory — the toast references which task synced.
    expect(r.task_id).toBe(fixture.expected.task_id);
    // ``synced`` is present (its truthy/falsy gates the toast color).
    expect(r).toHaveProperty('synced');
  });

  test('formatSyncResult accepts every real backend shape without throwing', () => {
    // ``formatSyncResult`` is the UI helper that turns the response
    // into a toast spec. It must handle ALL three real shapes.
    // We pass each as the second arg ({ok, body}) — same shape the
    // api.js wrapper produces from a fetch response.
    for (const key of ['no_workspace', 'task_lookup_failed', 'nothing_to_sync']) {
      const wrapped = { ok: true, body: fixture[key] };
      // Just calling it must not throw — the shape must be
      // recognisable to the renderer.
      expect(() => formatSyncResult(wrapped)).not.toThrow();
      const toast = formatSyncResult(wrapped);
      // Every toast spec has a kind ('success' / 'warning' / 'error')
      // and a message field — that's what the renderer consumes.
      expect(toast).toHaveProperty('kind');
      expect(toast).toHaveProperty('message');
    }
  });
});
