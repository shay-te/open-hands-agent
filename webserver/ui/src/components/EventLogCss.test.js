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

  assertDeclaration(textBody, 'white-space', 'pre-wrap');
  assertDeclaration(textBody, 'overflow-wrap', 'anywhere');
  assert.doesNotMatch(textBody, /text-overflow\s*:\s*ellipsis\s*;/);
  assert.doesNotMatch(textBody, /white-space\s*:\s*nowrap\s*;/);
});

test('EventLog sticky prompts collapse to three lines with snippet expand button', () => {
  const wrapBody = ruleBody('.chat-sticky-prompt-text-wrap.is-collapsed');
  const expandBody = ruleBody('.chat-sticky-prompt-expand');
  const fadeBody = ruleBody('.chat-sticky-prompt-text-wrap.is-collapsed::after');

  assertDeclaration(wrapBody, 'max-height', 'calc\\(12\\.5px \\* 1\\.5 \\* 3\\)');
  assertDeclaration(wrapBody, 'overflow', 'hidden');
  assertDeclaration(expandBody, 'bottom', '0');
  assert.match(fadeBody, /background\s*:\s*linear-gradient\(/);
});
