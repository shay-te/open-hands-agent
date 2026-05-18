import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
);

function ruleBody(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  assert.ok(match, `expected ${selector} rule to exist`);
  return match[1];
}

function assertDeclaration(body, property, value) {
  const declaration = new RegExp(`${property}\\s*:\\s*${value}\\s*;`);
  assert.match(body, declaration);
}

test('EventLog sticky prompts wrap instead of truncating to one line', () => {
  const textBody = ruleBody('.chat-sticky-prompt-text');
  const toggleBody = ruleBody('.chat-sticky-prompt-toggle');

  assertDeclaration(textBody, 'white-space', 'pre-wrap');
  assertDeclaration(textBody, 'overflow-wrap', 'anywhere');
  assertDeclaration(textBody, 'overflow-y', 'auto');
  assertDeclaration(toggleBody, 'align-items', 'flex-start');
  assert.doesNotMatch(textBody, /text-overflow\s*:\s*ellipsis\s*;/);
  assert.doesNotMatch(textBody, /white-space\s*:\s*nowrap\s*;/);
});
