// Backend ↔ UI contract test for /comments.
//
// Loads the captured JSON the real Flask backend produced (written
// by ``tests/test_comments_contract.py`` → ``__fixtures__/
// comments_contract.json``) and asserts the UI's consumers can read
// every field they depend on. The Python side asserts the
// backend produces the shape; this side asserts the UI's
// expectations stay aligned with the same bytes.
//
// No mocks of the comment-rendering UI surfaces — only the fixture
// shape is the contract. If a UI component starts depending on a
// new field, add an assertion here that the field is present.

import { describe, test, expect } from 'vitest';

import fixture from './__fixtures__/comments_contract.json';
import { buildFilesCommentMeta } from './FilesTab.jsx';

describe('/api/sessions/<task>/comments contract', () => {

  test('fixture has the required top-level shape', () => {
    expect(fixture.expected).toBeTruthy();
    expect(fixture.expected.task_id).toBeTruthy();
    expect(fixture.expected.repo_id).toBeTruthy();
    expect(fixture.list_empty).toBeTruthy();
    expect(fixture.list_after_create).toBeTruthy();
    expect(fixture.create).toBeTruthy();
  });

  test('GET /comments empty response is {comments: []}', () => {
    expect(fixture.list_empty).toEqual({ comments: [] });
  });

  test('POST /comments response carries the keys the UI consumes', () => {
    // The optimistic-update path in the comment composer reads
    // these fields off the response to link the pending row.
    const required = ['ok', 'comment'];
    for (const key of required) {
      expect(fixture.create).toHaveProperty(key);
    }
    expect(fixture.create.ok).toBe(true);
    const comment = fixture.create.comment;
    const commentKeys = [
      'id', 'repo_id', 'file_path', 'line', 'body',
      'author', 'source', 'status', 'kato_status', 'parent_id',
    ];
    for (const key of commentKeys) {
      expect(comment).toHaveProperty(key);
    }
    expect(comment.body).toBe(fixture.expected.body);
    expect(comment.repo_id).toBe(fixture.expected.repo_id);
    expect(comment.file_path).toBe(fixture.expected.file_path);
    expect(comment.line).toBe(fixture.expected.line);
  });

  test('GET /comments after create returns a list with the same shape', () => {
    const comments = fixture.list_after_create.comments;
    expect(Array.isArray(comments)).toBe(true);
    expect(comments.length).toBe(1);
    const c = comments[0];
    for (const key of ['id', 'repo_id', 'file_path', 'line', 'body',
                       'kato_status', 'source', 'status', 'parent_id']) {
      expect(c).toHaveProperty(key);
    }
  });

  test('buildFilesCommentMeta accepts the real backend payload', () => {
    // The UI helper reads ``comments`` to build per-file badges.
    // If a future field rename breaks this consumer the test fails
    // before any component renders against bad data.
    const meta = buildFilesCommentMeta(
      fixture.list_after_create.comments,
    );
    expect(meta instanceof Map).toBe(true);
    // The single comment is on ``client:src/app.py``; the meta
    // surfaces that file as having an open thread.
    expect(meta.size).toBeGreaterThan(0);
  });
});
