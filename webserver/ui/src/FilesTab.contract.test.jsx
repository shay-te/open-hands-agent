// Backend ↔ UI contract test.
//
// Loads the REAL JSON the Flask backend produced (captured to
// ``__fixtures__/files_diff_contract.json`` by
// ``tests/test_files_diff_contract.py``) and renders the REAL
// FilesTab against it. Mocks are limited to:
//   * api.js — stubbed to return the captured backend payload
//     verbatim (its job is what a real HTTP fetch would return).
//   * stores/toastStore.js — stubbed so toast notifications don't
//     try to mount into the test DOM. Not contract-relevant.
// Everything from ``normalizeTrees`` down — and FilesTab itself,
// and react-arborist's tree rendering — runs real.
//
// If the backend payload shape changes (a field renamed, dropped,
// or added), the Python contract test regenerates the fixture;
// running this test against the new fixture surfaces any UI
// expectation that no longer matches.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import fixture from './__fixtures__/files_diff_contract.json';

vi.mock('./api.js', () => ({
  // The two routes the contract covers — both serve the real
  // captured payload directly.
  fetchFileTree: vi.fn(),
  fetchDiff: vi.fn(),
  fetchFileContent: vi.fn(),
  fetchRepoCommits: vi.fn().mockResolvedValue({ ok: true, body: [] }),
  fetchTaskComments: vi.fn().mockResolvedValue(
    { ok: true, body: { comments: [] } },
  ),
  syncTaskRepositories: vi.fn(),
}));
vi.mock('./stores/toastStore.js', () => ({
  toast: { show: vi.fn() },
  toastResult: vi.fn(),
}));

import FilesTab from './FilesTab.jsx';
import { fetchFileTree, fetchDiff } from './api.js';

beforeEach(() => {
  // Serve the exact bytes the real Flask handler returned.
  fetchFileTree.mockResolvedValue(fixture.files);
  fetchDiff.mockResolvedValue(fixture.diff);
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  });
});


describe('FilesTab — contract with real Flask /files + /diff payloads', () => {

  test('fixture has the required top-level shape we depend on', () => {
    // Cheap sanity so a corrupt fixture surfaces ITS error before
    // we render and get a confusing React stack.
    expect(fixture.expected).toBeTruthy();
    expect(fixture.expected.task_id).toBeTruthy();
    expect(fixture.expected.repo_id).toBeTruthy();
    expect(Array.isArray(fixture.expected.changed_basenames)).toBe(true);
    expect(Array.isArray(fixture.files.trees)).toBe(true);
    expect(Array.isArray(fixture.diff.diffs)).toBe(true);
  });

  test('every trees[*] entry has the keys normalizeTrees consumes', () => {
    // Mirror of the Python-side assertion. If the backend drops a
    // field, this fails before any rendering happens — a clear
    // contract-level failure rather than a downstream null deref.
    const required = [
      'repo_id', 'cwd', 'tree', 'conflicted_files', 'changed_files',
    ];
    fixture.files.trees.forEach((entry, i) => {
      required.forEach((key) => {
        expect(entry).toHaveProperty(
          key,
          // type message below kicks in if missing
        );
        if (key === 'conflicted_files' || key === 'changed_files') {
          expect(
            Array.isArray(entry[key]),
            `trees[${i}].${key} must be an array`,
          ).toBe(true);
        }
      });
    });
  });

  test('every diffs[*] entry has the keys ChangesTab consumes', () => {
    const required = ['repo_id', 'cwd', 'base', 'head', 'diff'];
    fixture.diff.diffs.forEach((entry, i) => {
      required.forEach((key) => {
        expect(entry).toHaveProperty(key, expect.anything());
        if (key === 'diff') {
          expect(
            typeof entry[key],
            `diffs[${i}].diff must be a string`,
          ).toBe('string');
        }
      });
    });
  });

  test('FilesTab renders the changed files reported by the real backend', async () => {
    render(
      <FilesTab taskId={fixture.expected.task_id} onOpenFile={vi.fn()} />,
    );
    // Header confirms the diff load completed (one repo in the fixture).
    expect(await screen.findByText('Lines updated')).toBeInTheDocument();
    // Every basename the real backend reported as changed must
    // appear in the rendered tree.
    for (const base of fixture.expected.changed_basenames) {
      // eslint-disable-next-line no-await-in-loop
      const node = await screen.findByText(base);
      expect(node).toBeInTheDocument();
    }
  });

  test('right-clicking a real-backend file copies the repo-prefixed path', async () => {
    render(
      <FilesTab taskId={fixture.expected.task_id} onOpenFile={vi.fn()} />,
    );
    expect(await screen.findByText('Lines updated')).toBeInTheDocument();

    // Pick a real changed file (basename) from the fixture.
    const targetBase = fixture.expected.changed_basenames[0];
    const candidates = screen.queryAllByText(targetBase);
    const treeRow = candidates
      .map((n) => n.closest('button'))
      .find((b) => b !== null);
    expect(treeRow).not.toBeNull();
    fireEvent.contextMenu(treeRow);
    const copyItem = screen.queryByRole('menuitem', {
      name: 'Copy relative path',
    });
    expect(copyItem).not.toBeNull();
    fireEvent.click(copyItem);

    await waitFor(() => {
      const calls = navigator.clipboard.writeText.mock.calls;
      expect(calls.length).toBeGreaterThan(0);
      const payload = calls[calls.length - 1][0];
      expect(typeof payload).toBe('string');
      // "<repo_id>:<path>" — never an absolute /__fixture__/ leak.
      expect(payload.startsWith(`${fixture.expected.repo_id}:`)).toBe(true);
      expect(payload.includes(targetBase)).toBe(true);
      expect(payload.startsWith('/')).toBe(false);
    });
  });

  test('commits payload has the keys the Files-tab commits dropdown reads', () => {
    expect(fixture.commits).toBeTruthy();
    const required = ['repo_id', 'base', 'head', 'commits'];
    for (const key of required) {
      expect(fixture.commits).toHaveProperty(key);
    }
    expect(Array.isArray(fixture.commits.commits)).toBe(true);
    expect(fixture.commits.commits.length).toBeGreaterThan(0);
    const first = fixture.commits.commits[0];
    for (const key of ['sha', 'subject', 'author', 'epoch']) {
      expect(first).toHaveProperty(key);
    }
    // The commit message we put on the task branch is here.
    const subjects = fixture.commits.commits.map((c) => c.subject);
    for (const expected of fixture.expected.commit_subjects_include) {
      expect(subjects).toContain(expected);
    }
  });

  test('file payload has the keys the Monaco viewer reads', () => {
    expect(fixture.file).toBeTruthy();
    // ``content`` is mandatory for text files; ``binary`` is the
    // alternative shape for NUL-byte files.
    expect(
      Object.prototype.hasOwnProperty.call(fixture.file, 'content')
        || Object.prototype.hasOwnProperty.call(fixture.file, 'binary'),
    ).toBe(true);
    if (typeof fixture.file.content === 'string') {
      expect(fixture.file.content)
        .toContain(fixture.expected.file_text_includes);
    }
  });

  test('toggle to "all files" surfaces non-changed files alongside changed', async () => {
    // The real backend tree contains files NOT in changed_files.
    // The All toggle must reveal at least one of them. Catches a
    // bug where the toggle is wired up to a hardcoded list rather
    // than the real backend tree.
    render(
      <FilesTab taskId={fixture.expected.task_id} onOpenFile={vi.fn()} />,
    );
    expect(await screen.findByText('Lines updated')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show all files' }));
    // Expand the first folder we can find in the tree.
    const folderNodes = walkFolderNames(fixture.files.trees[0].tree);
    expect(folderNodes.length).toBeGreaterThan(0);
    for (const folderName of folderNodes) {
      const labels = screen.queryAllByText(folderName);
      const treeRow = labels
        .map((n) => n.closest('button'))
        .find((b) => b !== null);
      if (treeRow) fireEvent.click(treeRow);
    }
    // Now look for a file that's in the tree but NOT in changed_files.
    const allFileNames = walkFileNames(fixture.files.trees[0].tree);
    const changedBasenames = new Set(fixture.expected.changed_basenames);
    const unchangedBasenames = allFileNames
      .filter((name) => !changedBasenames.has(name));
    expect(unchangedBasenames.length).toBeGreaterThan(0);
    // At least one unchanged file is now visible.
    let found = false;
    for (const name of unchangedBasenames) {
      if (screen.queryByText(name)) { found = true; break; }
    }
    expect(found).toBe(true);
  });
});


function walkFolderNames(nodes) {
  const out = [];
  for (const node of nodes || []) {
    if (Array.isArray(node?.children) && node.children.length > 0) {
      out.push(node.name);
      out.push(...walkFolderNames(node.children));
    }
  }
  return out;
}

function walkFileNames(nodes) {
  const out = [];
  for (const node of nodes || []) {
    if (Array.isArray(node?.children) && node.children.length > 0) {
      out.push(...walkFileNames(node.children));
    } else if (node?.name) {
      out.push(node.name);
    }
  }
  return out;
}
