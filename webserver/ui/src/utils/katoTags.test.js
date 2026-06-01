import assert from 'node:assert/strict';
import test from 'node:test';

import {
  KATO_TAG_NAMESPACE,
  REPOSITORY_TAG_SEGMENT,
  REPOSITORY_TAG_PREFIX,
} from './katoTags.js';

test('tag constants keep their historical values (must match operators\' tags + the Python side)', () => {
  assert.equal(KATO_TAG_NAMESPACE, 'kato');
  assert.equal(REPOSITORY_TAG_SEGMENT, 'repo');
  // The prefix is composed from the namespace + segment — single source.
  assert.equal(REPOSITORY_TAG_PREFIX, 'kato:repo:');
  assert.equal(REPOSITORY_TAG_PREFIX, `${KATO_TAG_NAMESPACE}:${REPOSITORY_TAG_SEGMENT}:`);
});
