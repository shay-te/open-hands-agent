// Translates a Claude tool_use block into a human-readable rendering
// for the chat event log.
//
// Returns either:
//   - a plain string (the legacy header-only form), OR
//   - ``{ summary, details }`` where ``details`` is a multi-line
//     code/diff block to render under the header.
//
// The bubble renderer (EventLog) accepts both shapes.
//
// Design intent: full transparency. The operator sees the exact path
// the agent touched, the exact command it ran, and — for edits — the
// before/after snippet inline (the same surface Claude Code's CLI
// shows in its own UI). No path elision, no command truncation.

import { stringifyShort } from './dom.js';
import { countNoun } from './pluralize.js';


const FORMATTERS = {
  Bash: (input) => {
    const cmd = String(input?.command || '').trim();
    if (!cmd) { return '$ (empty)'; }
    // Multi-line commands (heredocs, scripts) render as a code
    // block under the header so the operator sees every line.
    const lines = cmd.split('\n');
    if (lines.length > 1) {
      return {
        summary: `$ ${lines[0]}`,
        details: lines.slice(1).join('\n'),
      };
    }
    return `$ ${cmd}`;
  },
  Read: (input) => `Read · ${String(input?.file_path || '')}`,
  Edit: (input) => {
    const path = String(input?.file_path || '');
    const oldStr = String(input?.old_string || '');
    const newStr = String(input?.new_string || '');
    const diff = formatEditDiff(oldStr, newStr);
    return {
      summary: `Edit · ${path}${_changeBadge(diff.stats)}`,
      details: diff.text,
    };
  },
  MultiEdit: (input) => {
    const path = String(input?.file_path || '');
    const edits = Array.isArray(input?.edits) ? input.edits : [];
    const editLabel = edits.length === 1 ? '1 edit' : `${edits.length} edits`;
    if (edits.length === 0) {
      return `Edit · ${path} (${editLabel})`;
    }
    const diffs = edits.map((edit) => {
      const oldStr = String(edit?.old_string || '');
      const newStr = String(edit?.new_string || '');
      return formatEditDiff(oldStr, newStr);
    });
    const totals = diffs.reduce(
      (acc, d) => ({ added: acc.added + d.stats.added, removed: acc.removed + d.stats.removed }),
      { added: 0, removed: 0 },
    );
    return {
      summary: `Edit · ${path} (${editLabel})${_changeBadge(totals)}`,
      details: diffs.map((d) => d.text).join('\n---\n'),
    };
  },
  Write: (input) => {
    const path = String(input?.file_path || '');
    const content = String(input?.content || '');
    if (!content) { return `Write · ${path}`; }
    return {
      summary: `Write · ${path}`,
      details: prefixLines(content, '+ '),
    };
  },
  NotebookEdit: (input) => {
    const path = String(input?.notebook_path || '');
    return `Notebook · ${path}`;
  },
  Glob: (input) => {
    const pattern = String(input?.pattern || '');
    const path = String(input?.path || '');
    if (path) { return `Glob · ${pattern} in ${path}`; }
    return `Glob · ${pattern}`;
  },
  Grep: (input) => {
    const pattern = String(input?.pattern || '');
    const path = String(input?.path || '');
    if (path) { return `Grep · "${pattern}" in ${path}`; }
    return `Grep · "${pattern}"`;
  },
  WebFetch: (input) => `WebFetch · ${String(input?.url || '')}`,
  WebSearch: (input) => `WebSearch · "${String(input?.query || '')}"`,
  Agent: (input) => {
    const subagent = String(input?.subagent_type || 'agent');
    const desc = String(input?.description || '');
    if (desc) { return `Agent (${subagent}) · ${desc}`; }
    return `Agent · ${subagent}`;
  },
  TodoWrite: (input) => {
    const todos = Array.isArray(input?.todos) ? input.todos : [];
    if (todos.length === 0) {
      return 'TodoWrite · 0 items';
    }
    // Show every todo with its status — operators want to see the
    // plan the agent is tracking.
    const lines = todos.map((todo) => {
      const status = String(todo?.status || 'pending');
      const content = String(todo?.content || todo?.activeForm || '');
      const marker = _statusMarker(status);
      return `${marker} ${content}`;
    });
    return {
      summary: `TodoWrite · ${countNoun(todos.length, 'item')}`,
      details: lines.join('\n'),
    };
  },
  KillShell: (input) => {
    const id = String(input?.shell_id || input?.task_id || '');
    return `KillShell${id ? ` · ${id}` : ''}`;
  },
  TaskOutput: (input) => {
    const id = String(input?.task_id || '');
    return `TaskOutput${id ? ` · ${id}` : ''}`;
  },
};


// Tools whose ``input`` names a single workspace file. Used by the
// chat bubble to offer a one-click "open this file" affordance next
// to the path the agent touched.
const FILE_PATH_TOOLS = new Set(['Read', 'Edit', 'MultiEdit', 'Write']);

export function toolUseFilePath(toolName, input) {
  if (!input || typeof input !== 'object') { return ''; }
  if (toolName === 'NotebookEdit') {
    return String(input.notebook_path || '').trim();
  }
  if (FILE_PATH_TOOLS.has(toolName)) {
    return String(input.file_path || '').trim();
  }
  return '';
}


export function formatToolUse(toolName, input) {
  const formatter = FORMATTERS[toolName];
  if (formatter) {
    try {
      return formatter(input || {});
    } catch (err) {
      if (typeof console !== 'undefined' && console.warn) {
        console.warn(`formatToolUse(${toolName}) threw:`, err);
      }
    }
  }
  // Unknown tool — fall back to legacy compact-JSON rendering.
  return `${toolName}(${stringifyShort(input)})`;
}


// Render an Edit's old → new replacement as a unified diff: unchanged
// lines kept as context, only differences tagged with ``+ `` / ``- ``.
// Matches the way Claude Code's own VS Code extension renders Edit
// tool calls, where the operator wants to see the SURROUNDING context
// alongside the actual change — not two separate dump-everything
// blocks. We compute an LCS-based line diff (small dimensions in
// practice — old_string / new_string are usually a few dozen lines),
// then walk the alignment producing context (``  ``), additions
// (``+ ``), and deletions (``- ``).
//
// Returns ``{ text, stats: {added, removed} }`` so the summary line
// can show "+N -M" alongside the path the way VS Code does.
function formatEditDiff(oldStr, newStr) {
  const oldLines = _splitForDiff(oldStr);
  const newLines = _splitForDiff(newStr);
  if (oldLines.length === 0 && newLines.length === 0) {
    return { text: '', stats: { added: 0, removed: 0 } };
  }
  const ops = _lcsLineDiff(oldLines, newLines);
  let added = 0;
  let removed = 0;
  const out = [];
  for (const op of ops) {
    if (op.kind === 'add') {
      added += 1;
      out.push(`+ ${op.text}`);
    } else if (op.kind === 'del') {
      removed += 1;
      out.push(`- ${op.text}`);
    } else {
      // Two-space context prefix keeps the column-zero character of
      // each line aligned with ``+ `` / ``- `` rows; the renderer
      // only special-cases ``+ `` and ``- ``, so context shows up
      // uncoloured.
      out.push(`  ${op.text}`);
    }
  }
  return { text: out.join('\n'), stats: { added, removed } };
}


function _splitForDiff(text) {
  const raw = String(text || '');
  if (!raw) { return []; }
  const lines = raw.split('\n');
  // Drop a single trailing blank line (string ending in \n) so an
  // ``old_string`` like "foo\n" doesn't render an extra empty row.
  if (lines.length > 1 && lines[lines.length - 1] === '') {
    lines.pop();
  }
  return lines;
}


function prefixLines(text, prefix) {
  const lines = _splitForDiff(text);
  if (lines.length === 0) { return ''; }
  return lines.map((line) => `${prefix}${line}`).join('\n');
}


// Standard LCS-based line diff. O(m·n) time and space — fine for the
// sizes we see in Edit tool calls (typically <200 lines per side; the
// Claude SDK rejects huge old_strings anyway).
function _lcsLineDiff(oldLines, newLines) {
  const m = oldLines.length;
  const n = newLines.length;
  // dp[i][j] = LCS length of oldLines[0..i] and newLines[0..j].
  const dp = Array.from({ length: m + 1 }, () => new Int32Array(n + 1));
  for (let i = 1; i <= m; i += 1) {
    for (let j = 1; j <= n; j += 1) {
      if (oldLines[i - 1] === newLines[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }
  const ops = [];
  let i = m;
  let j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
      ops.push({ kind: 'eq', text: oldLines[i - 1] });
      i -= 1; j -= 1;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      ops.push({ kind: 'add', text: newLines[j - 1] });
      j -= 1;
    } else {
      ops.push({ kind: 'del', text: oldLines[i - 1] });
      i -= 1;
    }
  }
  ops.reverse();
  return ops;
}


function _changeBadge(stats) {
  if (!stats) { return ''; }
  const added = Number(stats.added || 0);
  const removed = Number(stats.removed || 0);
  if (added === 0 && removed === 0) { return ''; }
  const parts = [];
  if (added > 0) { parts.push(`+${added}`); }
  if (removed > 0) { parts.push(`-${removed}`); }
  return ` · ${parts.join(' ')}`;
}


function _statusMarker(status) {
  switch (status) {
    case 'completed': return '✓';
    case 'in_progress': return '→';
    case 'cancelled': return '✗';
    default: return '·';
  }
}
