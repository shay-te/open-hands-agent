// Tests for the pure diff model + tree helpers. These moved out of
// the deleted ChangesTab component into ``diffModel.js``; only the
// pure logic survived, so these tests no longer render anything.

import { describe, test, expect } from 'vitest';

import { basenameOf } from './utils/basenameOf.js';
import {
  buildDiffFileTree,
  changedFileOpenTarget,
  countFileChangeStats,
  diffDisplayPath,
  diffFileKey,
  diffLabelForStatus,
  parseRepoDiffs,
} from './diffModel.js';

const _UNIFIED_DIFF = [
  'diff --git a/src/a.js b/src/a.js',
  'index 0000000..1111111 100644',
  '--- a/src/a.js',
  '+++ b/src/a.js',
  '@@ -1 +1 @@',
  '-old',
  '+new',
  '',
].join('\n');


describe('diffLabelForStatus — maps server status to header chip', () => {

  test('review → "already pushed (PR open)"', () => {
    expect(diffLabelForStatus('review')).toBe('already pushed (PR open)');
  });

  test('done → "merged"', () => {
    expect(diffLabelForStatus('done')).toBe('merged');
  });

  test('errored → "publish errored"', () => {
    expect(diffLabelForStatus('errored')).toBe('publish errored');
  });

  test('terminated → "terminated"', () => {
    expect(diffLabelForStatus('terminated')).toBe('terminated');
  });

  test('unknown / empty status → "" (no chip)', () => {
    expect(diffLabelForStatus('')).toBe('');
    expect(diffLabelForStatus('active')).toBe('');
    expect(diffLabelForStatus(undefined)).toBe('');
    expect(diffLabelForStatus(null)).toBe('');
  });

  test('case-insensitive matching', () => {
    expect(diffLabelForStatus('REVIEW')).toBe('already pushed (PR open)');
    expect(diffLabelForStatus('Done')).toBe('merged');
  });
});


describe('countFileChangeStats — additions/deletions for badges', () => {

  test('counts inserted and deleted hunk changes', () => {
    const stats = countFileChangeStats({
      hunks: [{
        changes: [
          { type: 'insert' },
          { isInsert: true },
          { type: 'delete' },
          { type: 'normal' },
        ],
      }],
    });
    expect(stats).toEqual({ added: 2, deleted: 1 });
  });
});


describe('buildDiffFileTree — Bitbucket-style path grouping', () => {

  test('compresses single-child folders and aggregates line stats', () => {
    const tree = buildDiffFileTree([
      {
        type: 'modify',
        oldPath: 'src/assets/locale/en/i18n.js',
        newPath: 'src/assets/locale/en/i18n.js',
        hunks: [{ changes: [{ type: 'insert' }, { type: 'delete' }] }],
      },
      {
        type: 'add',
        oldPath: '/dev/null',
        newPath: 'src/assets/locale/he/i18n.js',
        hunks: [{ changes: [{ type: 'insert' }] }],
      },
      {
        type: 'add',
        oldPath: '/dev/null',
        newPath: 'src/network/networkPromise.js',
        hunks: [{ changes: [{ type: 'insert' }, { type: 'insert' }] }],
      },
    ]);

    expect(tree.stats).toEqual({ added: 4, deleted: 1 });
    expect(tree.nodes[0].name).toBe('src');
    expect(tree.nodes[0].children[0].name).toBe('assets/locale');
    expect(tree.nodes[0].children[1].name).toBe('network');
  });
});


describe('basenameOf — derives the last path segment', () => {

  test('forward-slash path', () => {
    expect(basenameOf('/workspaces/PROJ-1/client')).toBe('client');
  });

  test('backslash path (Windows-style)', () => {
    expect(basenameOf('C:\\workspaces\\PROJ-1\\client')).toBe('client');
  });

  test('trailing slash stripped before extraction', () => {
    expect(basenameOf('/workspaces/PROJ-1/client/')).toBe('client');
  });

  test('empty / null returns empty string', () => {
    expect(basenameOf('')).toBe('');
    expect(basenameOf(null)).toBe('');
    expect(basenameOf(undefined)).toBe('');
  });

  test('single-segment path returns itself', () => {
    expect(basenameOf('client')).toBe('client');
  });
});


describe('diffFileKey — stable identity for react-diff-view keying', () => {

  test('uses type + oldPath + newPath', () => {
    const key = diffFileKey({
      type: 'modify', oldPath: 'src/a.py', newPath: 'src/a.py',
    });
    expect(key).toContain('modify');
    expect(key).toContain('src/a.py');
  });

  test('rename: old and new paths differ', () => {
    const key = diffFileKey({
      type: 'rename', oldPath: 'src/old.py', newPath: 'src/new.py',
    });
    expect(key).toContain('src/old.py');
    expect(key).toContain('src/new.py');
  });

  test('add: only newPath relevant', () => {
    const key = diffFileKey({
      type: 'add', oldPath: '', newPath: 'src/new.py',
    });
    expect(key).toContain('add');
    expect(key).toContain('src/new.py');
  });

  test('delete: only oldPath relevant', () => {
    const key = diffFileKey({
      type: 'delete', oldPath: 'src/old.py', newPath: '',
    });
    expect(key).toContain('delete');
    expect(key).toContain('src/old.py');
  });

  test('two different files produce different keys', () => {
    const a = diffFileKey({
      type: 'modify', oldPath: 'src/a.py', newPath: 'src/a.py',
    });
    const b = diffFileKey({
      type: 'modify', oldPath: 'src/b.py', newPath: 'src/b.py',
    });
    expect(a).not.toBe(b);
  });
});


describe('diffDisplayPath — real path, never /dev/null', () => {

  test('delete shows the OLD path, not /dev/null (the screenshot bug)', () => {
    expect(diffDisplayPath({
      type: 'delete', oldPath: 'src/gone.js', newPath: '/dev/null',
    })).toBe('src/gone.js');
  });

  test('add shows the new path (old side is /dev/null)', () => {
    expect(diffDisplayPath({
      type: 'add', oldPath: '/dev/null', newPath: 'src/new.js',
    })).toBe('src/new.js');
  });

  test('modify / rename use the new path', () => {
    expect(diffDisplayPath({
      type: 'modify', oldPath: 'a.js', newPath: 'a.js',
    })).toBe('a.js');
    expect(diffDisplayPath({
      type: 'rename', oldPath: 'old.js', newPath: 'new.js',
    })).toBe('new.js');
  });

  test('both sides missing → "(unknown)"', () => {
    expect(diffDisplayPath({ type: 'modify' })).toBe('(unknown)');
    expect(diffDisplayPath({
      type: 'delete', oldPath: '/dev/null', newPath: '/dev/null',
    })).toBe('(unknown)');
  });
});


describe('parseRepoDiffs — wire payload → uniform per-repo list', () => {

  test('new "diffs: [...]" envelope: one entry per repo, diff parsed', () => {
    const repos = parseRepoDiffs({
      diffs: [{
        repo_id: 'client',
        cwd: '/w/client',
        base: 'main',
        head: 'feat',
        diff: _UNIFIED_DIFF,
        conflicted_files: ['src/a.js'],
      }],
    });
    expect(repos).toHaveLength(1);
    expect(repos[0].repo_id).toBe('client');
    expect(repos[0].base).toBe('main');
    expect(repos[0].files.length).toBe(1);
    expect(repos[0].conflictedFiles.has('src/a.js')).toBe(true);
  });

  test('legacy flat shape: single repo, repo_id derived from cwd basename', () => {
    const repos = parseRepoDiffs({ cwd: '/w/backend', diff: _UNIFIED_DIFF });
    expect(repos).toHaveLength(1);
    expect(repos[0].repo_id).toBe('backend');
    expect(repos[0].files.length).toBe(1);
  });

  test('empty diff string → no files, no crash', () => {
    const repos = parseRepoDiffs({ diffs: [{ repo_id: 'r', cwd: '/w/r', diff: '' }] });
    expect(repos[0].files).toEqual([]);
    expect(repos[0].conflictedFiles.size).toBe(0);
  });

  test('identical (repo, raw) is served from the parse cache', () => {
    const a = parseRepoDiffs({ diffs: [{ repo_id: 'c', cwd: '/w/c', diff: _UNIFIED_DIFF }] });
    const b = parseRepoDiffs({ diffs: [{ repo_id: 'c', cwd: '/w/c', diff: _UNIFIED_DIFF }] });
    // Cache hit returns the very same parsed array reference.
    expect(a[0].files).toBe(b[0].files);
  });
});


describe('changedFileOpenTarget — payload a row hands to the centre pane', () => {

  test('joins repo cwd + diff path into an absolute path, view=diff', () => {
    const t = changedFileOpenTarget(
      { repo_id: 'client', cwd: '/w/client' },
      { type: 'modify', newPath: 'src/App.jsx', oldPath: 'src/App.jsx' },
    );
    expect(t).toEqual({
      absolutePath: '/w/client/src/App.jsx',
      relativePath: 'src/App.jsx',
      repoId: 'client',
      view: 'diff',
    });
  });

  test('uses newPath; trailing slashes on cwd are normalised', () => {
    const t = changedFileOpenTarget(
      { repo_id: 'r', cwd: '/w/r//' },
      { type: 'add', newPath: 'a/b.js', oldPath: '/dev/null' },
    );
    expect(t.absolutePath).toBe('/w/r/a/b.js');
    expect(t.relativePath).toBe('a/b.js');
  });

  test('falls back to oldPath for a delete', () => {
    const t = changedFileOpenTarget(
      { repo_id: 'r', cwd: '/w/r' },
      { type: 'delete', oldPath: 'gone.txt' },
    );
    expect(t.relativePath).toBe('gone.txt');
    expect(t.absolutePath).toBe('/w/r/gone.txt');
  });

  test('no cwd → relative path is used as-is (no leading slash)', () => {
    const t = changedFileOpenTarget(
      { repo_id: 'r', cwd: '' },
      { type: 'modify', newPath: 'x.js' },
    );
    expect(t.absolutePath).toBe('x.js');
  });
});
