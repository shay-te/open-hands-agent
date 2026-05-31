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

function ruleBodyContaining(selector, text) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const matches = [...css.matchAll(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 'g'))];
  const match = matches.find((entry) => {
    return entry[1].includes(text);
  });
  assert.ok(match, `expected ${selector} rule containing ${text} to exist`);
  return match[1];
}

function assertDeclaration(body, property, value) {
  const declaration = new RegExp(`${property}\\s*:\\s*${value}\\s*;`);
  assert.match(body, declaration);
}

test('DiffPane file cards clip diff rows inside rounded corners', () => {
  const body = ruleBody('.diff-pane .diff-file');
  assertDeclaration(body, 'overflow', 'clip');
});

test('Shared sticky section header utility owns sticky mechanics', () => {
  const body = ruleBody('.sticky-section-header');
  assertDeclaration(body, 'position', 'sticky');
  assertDeclaration(body, 'top', 'var\\(--sticky-header-top, 0\\)');
  assertDeclaration(body, 'z-index', 'var\\(--sticky-header-z, 4\\)');
});

test('DiffPane file headers keep their visual styling on the shared sticky header', () => {
  const body = ruleBody('.diff-pane .diff-file-header');
  assertDeclaration(body, '--sticky-header-z', '3');
  assertDeclaration(body, 'background', '#2a2a2a');
});

test('DiffPane uses compact side spacing around file cards and headers', () => {
  const paneBody = ruleBody('.diff-pane-body');
  const headerBody = ruleBody('.diff-pane .diff-file-header');
  assertDeclaration(paneBody, 'padding', '0 6px');
  assertDeclaration(headerBody, 'padding', '6px');
});

test('Badge, chip, and pill classes share the global pill radius', () => {
  const rootBody = ruleBody(':root');
  // Sass normalizes attribute-selector quoting ([class*="badge"] ->
  // [class*=badge]); both are equivalent CSS, so match the unquoted form.
  const safetyBody = ruleBody(':where([class*=badge], [class*=chip], [class*=pill])');
  assertDeclaration(rootBody, '--radius-pill', '999px');
  assertDeclaration(safetyBody, 'border-radius', 'var\\(--radius-pill\\)');

  const cssWithoutComments = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const rulePattern = /([^{}]+)\{([^{}]*)\}/g;
  const failures = [];
  for (const match of cssWithoutComments.matchAll(rulePattern)) {
    const selector = match[1].trim();
    const body = match[2];
    const isPillish = /(?:badge|chip|pill)/.test(selector);
    const radius = body.match(/border-radius\s*:\s*([^;]+)\s*;/);
    if (!isPillish || !radius) { continue; }
    if (!/^(var\(--radius-pill\)|999px)$/.test(radius[1].trim())) {
      failures.push(`${selector} -> ${radius[1].trim()}`);
    }
  }
  assert.deepEqual(failures, []);
});

test('DiffPane file headers draw a rounded face above scrolling diff rows', () => {
  const body = ruleBody('.diff-pane .diff-file-header::before');
  assertDeclaration(body, 'background', '#0a0a0a');
  assertDeclaration(body, 'border', '1px solid #2a2a2a');
  assertDeclaration(body, 'border-radius', '10px 10px 0 0');
});

test('Collapsed diff file header rounds all corners', () => {
  const body = ruleBody('.diff-pane .diff-file.is-collapsed .diff-file-header::before');
  assertDeclaration(body, 'border-radius', '10px');
});

test('DiffPane uses muted Bitbucket-style hunk colors', () => {
  const body = ruleBodyContaining('.diff-file', '--diff-code-insert-background-color');
  assertDeclaration(body, '--diff-code-insert-background-color', '#1d2b27');
  assertDeclaration(body, '--diff-gutter-insert-background-color', '#1d2b27');
  assertDeclaration(body, '--diff-code-delete-background-color', '#2d1f22');
  assertDeclaration(body, '--diff-gutter-delete-background-color', '#2d1f22');
});

test('Diff file comments panel rounds the bottom of the file card', () => {
  const body = ruleBody('.diff-file-comments');
  assertDeclaration(body, 'border-radius', '0 0 10px 10px');
});

test('Diff file header keeps the expand/collapse chevron on the left', () => {
  const body = ruleBody('.diff-file-header .diff-file-collapse-toggle');
  assertDeclaration(body, 'margin-left', '0');
  assertDeclaration(body, 'flex-shrink', '0');
});

test('Diff file path button does not inherit the global round header button skin', () => {
  const body = ruleBody('.diff-file-header .diff-file-path-button');
  const hoverBody = ruleBody('.diff-file-header .diff-file-path-button:hover');
  assertDeclaration(body, 'width', 'auto');
  assertDeclaration(body, 'height', 'auto');
  assertDeclaration(body, 'border', '0');
  assertDeclaration(body, 'border-radius', '0');
  assertDeclaration(body, 'background', 'transparent');
  assertDeclaration(body, 'box-shadow', 'none');
  assertDeclaration(hoverBody, 'border', '0');
  assertDeclaration(hoverBody, 'background', 'transparent');
  assertDeclaration(hoverBody, 'box-shadow', 'none');
});

test('Files tab body scrolls changed-file trees vertically', () => {
  const body = ruleBody('.files-tab-body');
  assertDeclaration(body, 'overflow-y', 'auto');
  assertDeclaration(body, 'overflow-x', 'hidden');
});

test('Files tab repo headers stick while scrolling a repository', () => {
  const repoBody = ruleBody('.files-tab-repo');

  assertDeclaration(repoBody, 'overflow', 'visible');
});

test('Changed-file tree guide line stays out of the chevron lane', () => {
  const body = ruleBody('.diff-file-tree-guide');
  assertDeclaration(body, 'left', '22px');
  assert.match(body, /width\s*:\s*calc\(var\(--depth\) \* 22px\)\s*;/);
  assert.match(body, /background-image\s*:\s*repeating-linear-gradient\(/);
});

test('Changed-file tree gives folders lighter weight than files', () => {
  const folderBody = ruleBody('.files-changed-tree-folder');
  const fileBody = ruleBody('.files-changed-tree-label');
  assertDeclaration(folderBody, 'font-weight', '600');
  assertDeclaration(fileBody, 'font-weight', '750');
});

test('Changed-file tree hover and selected states use opaque backgrounds', () => {
  const hoverBody = ruleBody('.diff-file-tree-row.is-file:hover');
  const selectedBody = ruleBody('.diff-file-tree-row.selected');
  const selectedHoverBody = ruleBody('.diff-file-tree-row.is-file.selected:hover');

  assertDeclaration(hoverBody, 'background', '#2a2a2a');
  assertDeclaration(selectedBody, 'background', '#1f2937');
  assertDeclaration(selectedHoverBody, 'background', '#1f2937');
});

test('Diff syntax colors JSX and stylesheet tokens like Bitbucket', () => {
  const tagBody = ruleBody('.diff-file .token.tag');
  const attrNameBody = ruleBody('.diff-file .token.attr-name');
  const selectorBody = ruleBody('.diff-file .token.selector');
  const propertyBody = ruleBody('.diff-file .token.property');
  const propertyAccessBody = ruleBody('.diff-file .token.property-access');
  const variableBody = ruleBody('.diff-file .token.variable');
  const stringBody = ruleBodyContaining('.diff-file .token.string', '#f59e0b');
  const keywordBody = ruleBodyContaining('.diff-file .token.keyword', '#fca5a5');

  assertDeclaration(tagBody, 'color', '#0a84ff');
  assertDeclaration(attrNameBody, 'color', '#79f2c0');
  assertDeclaration(selectorBody, 'color', '#0a84ff');
  assertDeclaration(propertyBody, 'color', '#79f2c0');
  assertDeclaration(propertyAccessBody, 'color', '#79f2c0');
  assertDeclaration(variableBody, 'color', '#cce0ff');
  assertDeclaration(stringBody, 'color', '#f59e0b');
  assertDeclaration(keywordBody, 'color', '#fca5a5');
});

test('Bitbucket comment card: avatar, collapse chevron, dot actions', () => {
  const avatar = ruleBody('.diff-file-comment-avatar');
  assertDeclaration(avatar, 'border-radius', '50%');

  const sourceBadge = ruleBody('.diff-file-comment-source');
  assertDeclaration(sourceBadge, 'border-radius', 'var\\(--radius-pill\\)');

  const statusPill = ruleBody('.diff-file-comment-pill');
  assertDeclaration(statusPill, 'border-radius', 'var\\(--radius-pill\\)');

  const collapse = ruleBody('.diff-file-comment-collapse');
  assertDeclaration(collapse, 'cursor', 'pointer');
  assertDeclaration(collapse, 'margin-left', 'auto');

  // Collapsed bubble state rule must exist.
  ruleBody('.diff-file-comment.is-collapsed');

  // Actions are middot-separated Bitbucket-style.
  const sep = ruleBody(
    '.diff-file-comment-action + .diff-file-comment-action::before',
  );
  // Sass emits string values with double quotes (content: '·' -> "·").
  assertDeclaration(sep, 'content', '"·"');
});

test('Comment editor has a formatting toolbar', () => {
  const btn = ruleBody('.diff-file-comments-toolbar-btn');
  assertDeclaration(btn, 'cursor', 'pointer');
  ruleBody('.diff-file-comments-toolbar');
});

test('Diff context expander has Bitbucket-style controls', () => {
  const rowBody = ruleBody('.diff-context-expander-inner');
  const buttonBody = ruleBody('.diff-context-expander-btn');

  assertDeclaration(rowBody, 'background', '#2a2a2a');
  assertDeclaration(rowBody, 'font-family', 'ui-monospace, monospace');
  assertDeclaration(buttonBody, 'width', '22px');
  assertDeclaration(buttonBody, 'border-radius', '4px');
});
