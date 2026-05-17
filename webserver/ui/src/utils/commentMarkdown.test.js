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
