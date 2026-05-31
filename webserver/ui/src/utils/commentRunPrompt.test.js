import assert from 'node:assert/strict';
import test from 'node:test';

import { parseCommentRunPrompt } from './commentRunPrompt.js';

const HEADER = 'Operator-added review comment from the kato diff tab.';

function prompt(fileLine) {
  return `${HEADER}\n\n${fileLine}\n\nComment: please fix this`;
}

test('parseCommentRunPrompt: pulls file + line from a comment-run prompt', () => {
  assert.deepEqual(
    parseCommentRunPrompt(prompt('File: `src/app/main.js` (line 42)')),
    { file: 'src/app/main.js', line: 42 },
  );
});

test('parseCommentRunPrompt: line defaults to 0 when the prompt omits it', () => {
  assert.deepEqual(
    parseCommentRunPrompt(prompt('File: `src/app/main.js`')),
    { file: 'src/app/main.js', line: 0 },
  );
});

test('parseCommentRunPrompt: tolerates paths with spaces', () => {
  assert.deepEqual(
    parseCommentRunPrompt(prompt('File: `src/some dir/a b.js` (line 3)')),
    { file: 'src/some dir/a b.js', line: 3 },
  );
});

test('parseCommentRunPrompt: file-level comment (bare path, no backticks) → line 0', () => {
  // The backend drops the backticks + line for a file-level comment.
  assert.deepEqual(
    parseCommentRunPrompt(prompt('File: src/app/main.js')),
    { file: 'src/app/main.js', line: 0 },
  );
});

test('parseCommentRunPrompt: null for the no-file placeholder', () => {
  assert.equal(parseCommentRunPrompt(prompt('File: (no file specified)')), null);
});

test('parseCommentRunPrompt: null when the header is absent', () => {
  assert.equal(
    parseCommentRunPrompt('File: `src/app/main.js` (line 42)\nComment: hi'),
    null,
  );
});

test('parseCommentRunPrompt: null when there is no File: line', () => {
  assert.equal(parseCommentRunPrompt(`${HEADER}\n\nComment: just a question?`), null);
});

test('parseCommentRunPrompt: null for empty / nullish input', () => {
  assert.equal(parseCommentRunPrompt(''), null);
  assert.equal(parseCommentRunPrompt(null), null);
  assert.equal(parseCommentRunPrompt(undefined), null);
});

test('parseCommentRunPrompt: does not match an ordinary operator prompt', () => {
  assert.equal(parseCommentRunPrompt('please refactor the File: `x` reference'), null);
});
