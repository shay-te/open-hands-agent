import { test } from 'node:test';
import assert from 'node:assert/strict';

import { tokenizeInline, parseBlocks } from './commentMarkdown.js';

test('tokenizeInline splits bold / italic / code / link / text', () => {
  assert.deepEqual(tokenizeInline('a **b** c'), [
    { type: 'text', value: 'a ' },
    { type: 'bold', value: 'b' },
    { type: 'text', value: ' c' },
  ]);
  assert.deepEqual(tokenizeInline('`x` _y_'), [
    { type: 'code', value: 'x' },
    { type: 'text', value: ' ' },
    { type: 'italic', value: 'y' },
  ]);
  assert.deepEqual(tokenizeInline('see [docs](https://x.io/a)'), [
    { type: 'text', value: 'see ' },
    { type: 'link', value: 'docs', href: 'https://x.io/a' },
  ]);
});

test('tokenizeInline keeps emphasis literal inside code spans', () => {
  assert.deepEqual(tokenizeInline('`**not bold**`'), [
    { type: 'code', value: '**not bold**' },
  ]);
});

test('tokenizeInline leaves intra-word underscores in identifiers alone', () => {
  // Regression: ``linked_entity_type`` was being rendered as
  // ``linked<em>entity</em>type`` (looked like ``linkedentitytype``
  // in the UI) because ``_x_`` matched the italic pattern even
  // when surrounded by word characters. CommonMark says ``_``
  // does NOT open or close emphasis intra-word.
  assert.deepEqual(tokenizeInline('linked_entity_type'), [
    { type: 'text', value: 'linked_entity_type' },
  ]);
  assert.deepEqual(tokenizeInline('a linked_task_id b'), [
    { type: 'text', value: 'a linked_task_id b' },
  ]);
});

test('tokenizeInline still treats free-standing _italic_ as italic', () => {
  assert.deepEqual(tokenizeInline('a _italic_ b'), [
    { type: 'text', value: 'a ' },
    { type: 'italic', value: 'italic' },
    { type: 'text', value: ' b' },
  ]);
});

test('parseBlocks classifies code / quote / lists / paragraphs', () => {
  const blocks = parseBlocks([
    'first para',
    '',
    '```',
    'code line',
    '```',
    '> a quote',
    '- one',
    '- two',
    '1. step',
  ].join('\n'));
  assert.deepEqual(blocks.map((b) => b.type), [
    'p', 'code', 'quote', 'ul', 'ol',
  ]);
  assert.equal(blocks[1].value, 'code line');
  assert.deepEqual(blocks[3].items, ['one', 'two']);
});

test('parseBlocks flags an empty body', () => {
  assert.deepEqual(parseBlocks('   '), [{ type: 'empty' }]);
});

test('parseBlocks recognises --- / *** / ___ as horizontal rule', () => {
  // Regression: Claude uses ``---`` as a section break in dense
  // replies. Previously it fell through to the paragraph branch and
  // rendered as literal dashes, gluing the reply into one wall of
  // text.
  for (const hr of ['---', '----', '***', '___', '   ---  ']) {
    const blocks = parseBlocks(`before\n${hr}\nafter`);
    assert.deepEqual(blocks.map((b) => b.type), ['p', 'hr', 'p']);
  }
});

test('parseBlocks recognises ATX headings # through ######', () => {
  const blocks = parseBlocks([
    '# top',
    '## sub',
    '###### bottom',
    'body line',
  ].join('\n'));
  assert.deepEqual(blocks, [
    { type: 'h', level: 1, value: 'top' },
    { type: 'h', level: 2, value: 'sub' },
    { type: 'h', level: 6, value: 'bottom' },
    { type: 'p', value: 'body line' },
  ]);
});

test('parseBlocks does NOT treat # mid-paragraph as a heading', () => {
  // ``# code comment in a paragraph`` must stay paragraph text —
  // ATX headings need the ``#`` at column zero.
  const blocks = parseBlocks('look at # this comment in the code');
  assert.deepEqual(blocks.map((b) => b.type), ['p']);
});
