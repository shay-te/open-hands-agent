import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  TOOL_DETAILS_HARD_CAP,
  computeEventLogWindow,
  computeToolDetailsRender,
} from './eventLogTruncation.js';

// These rules cap the worst-case DOM rendered by EventLog +
// ToolDetails. Pinned here so a future tweak (lower the threshold,
// raise the cap, change the unit) can't silently regress operator
// performance on long sessions or huge tool outputs.

test('computeToolDetailsRender keeps short output intact at any expansion state', () => {
  const lines = ['a', 'b', 'c'];
  assert.deepEqual(
    computeToolDetailsRender(lines, false),
    { visible: lines, overflowed: false },
  );
  assert.deepEqual(
    computeToolDetailsRender(lines, true),
    { visible: lines, overflowed: false },
  );
});

test('computeToolDetailsRender collapses to head when not expanded and over threshold', () => {
  const lines = Array.from({ length: 100 }, (_, i) => `line ${i}`);
  const result = computeToolDetailsRender(lines, false);
  assert.equal(result.visible.length, 40);
  assert.equal(result.visible[0], 'line 0');
  assert.equal(result.visible[39], 'line 39');
  assert.equal(result.overflowed, false);
});

test('computeToolDetailsRender hard-caps even when expanded so massive output cannot lock the browser', () => {
  const lines = Array.from({ length: 5000 }, (_, i) => `line ${i}`);
  const result = computeToolDetailsRender(lines, true);
  // Hard cap is 1000 — we never render more even with the operator
  // having clicked "show full output".
  assert.equal(result.visible.length, TOOL_DETAILS_HARD_CAP);
  assert.equal(result.overflowed, true);
});

test('computeToolDetailsRender does not flag overflow when expanded output fits under the cap', () => {
  const lines = Array.from({ length: 500 }, (_, i) => `line ${i}`);
  const result = computeToolDetailsRender(lines, true);
  assert.equal(result.visible.length, 500);
  assert.equal(result.overflowed, false);
});

test('computeEventLogWindow returns full list under threshold', () => {
  const entries = Array.from({ length: 50 }, (_, i) => ({ i }));
  const result = computeEventLogWindow(entries, false);
  assert.equal(result.visible.length, 50);
  assert.equal(result.hidden, 0);
});

test('computeEventLogWindow shows the most recent window when not showing all', () => {
  const entries = Array.from({ length: 1000 }, (_, i) => ({ i }));
  const result = computeEventLogWindow(entries, false);
  // Default window is the trailing 200 — i.e. the operator sees the
  // newest events, which is what a chat scrolled to bottom expects.
  assert.equal(result.visible.length, 200);
  assert.equal(result.visible[0].i, 800);
  assert.equal(result.visible[199].i, 999);
  assert.equal(result.hidden, 800);
});

test('computeEventLogWindow returns full list when showAll is set', () => {
  const entries = Array.from({ length: 1000 }, (_, i) => ({ i }));
  const result = computeEventLogWindow(entries, true);
  assert.equal(result.visible.length, 1000);
  assert.equal(result.hidden, 0);
});
