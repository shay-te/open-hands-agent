import assert from 'node:assert/strict';
import test from 'node:test';

import { pluralize, countNoun } from './pluralize.js';


test('pluralize: singular for count of 1, plural otherwise', () => {
  assert.equal(pluralize(1, 'image'), 'image');
  assert.equal(pluralize(0, 'image'), 'images');
  assert.equal(pluralize(3, 'image'), 'images');
});

test('pluralize: honours an explicit irregular plural', () => {
  assert.equal(pluralize(2, 'entry', 'entries'), 'entries');
  assert.equal(pluralize(1, 'entry', 'entries'), 'entry');
});

test('countNoun: prefixes the count', () => {
  assert.equal(countNoun(1, 'image'), '1 image');
  assert.equal(countNoun(3, 'image'), '3 images');
});
