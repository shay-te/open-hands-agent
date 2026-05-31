import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildCommentStatusByLocation,
  commentStatusKey,
  moreUrgentCommentStatus,
} from './commentStatus.js';

test('commentStatusKey: file + line, trimmed and numeric', () => {
  assert.equal(commentStatusKey('src/a.js', 12), 'src/a.js::12');
  assert.equal(commentStatusKey('  src/a.js  ', '12'), 'src/a.js::12');
});

test('commentStatusKey: missing / non-positive line normalises to 0', () => {
  assert.equal(commentStatusKey('a', 0), 'a::0');
  assert.equal(commentStatusKey('a', null), 'a::0');
  assert.equal(commentStatusKey('a', undefined), 'a::0');
  // File-level comments are stored as -1 by the backend; the prompt omits
  // the line and parses to 0. Both must land on the same key.
  assert.equal(commentStatusKey('a', -1), 'a::0');
  assert.equal(commentStatusKey('a', -1), commentStatusKey('a', 0));
});

test('moreUrgentCommentStatus: failed beats addressed; queued beats addressed', () => {
  assert.equal(moreUrgentCommentStatus('addressed', 'failed'), 'failed');
  assert.equal(moreUrgentCommentStatus('queued', 'addressed'), 'queued');
});

test('moreUrgentCommentStatus: unknown / empty rank last', () => {
  assert.equal(moreUrgentCommentStatus('addressed', 'mystery'), 'addressed');
  assert.equal(moreUrgentCommentStatus('', 'queued'), 'queued');
});

test('buildCommentStatusByLocation: keys root comments by file::line', () => {
  const map = buildCommentStatusByLocation([
    { file_path: 'src/a.js', line: 5, kato_status: 'in_progress' },
    { file_path: 'src/b.js', line: 9, kato_status: 'addressed' },
  ]);
  assert.equal(map.get('src/a.js::5'), 'in_progress');
  assert.equal(map.get('src/b.js::9'), 'addressed');
});

test('buildCommentStatusByLocation: skips replies, blank status, blank path', () => {
  const map = buildCommentStatusByLocation([
    { file_path: 'src/a.js', line: 5, kato_status: 'queued', parent_id: 'root-1' },
    { file_path: 'src/a.js', line: 6, kato_status: '' },
    { file_path: '', line: 7, kato_status: 'queued' },
  ]);
  assert.equal(map.size, 0);
});

test('buildCommentStatusByLocation: a file-level comment (line -1) keys at ::0', () => {
  const map = buildCommentStatusByLocation([
    { file_path: 'src/a.js', line: -1, kato_status: 'failed' },
  ]);
  assert.equal(map.get(commentStatusKey('src/a.js', 0)), 'failed');
});

test('buildCommentStatusByLocation: collision on one line keeps the more urgent', () => {
  const map = buildCommentStatusByLocation([
    { file_path: 'src/a.js', line: 5, kato_status: 'addressed' },
    { file_path: 'src/a.js', line: 5, kato_status: 'failed' },
  ]);
  assert.equal(map.get('src/a.js::5'), 'failed');
});

test('buildCommentStatusByLocation: empty for nullish input', () => {
  assert.equal(buildCommentStatusByLocation(null).size, 0);
  assert.equal(buildCommentStatusByLocation(undefined).size, 0);
  assert.equal(buildCommentStatusByLocation([]).size, 0);
});
