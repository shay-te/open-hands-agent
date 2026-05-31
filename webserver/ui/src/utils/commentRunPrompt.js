// Detect a kato comment-run prompt and pull out the file it targets, so
// the chat's sticky "You asked" prompt can offer a jump-to-comment icon.
//
// kato builds these prompts in ``AgentService._comment_agent_prompt``
// (Python): the first line is a fixed header, then a ``File:`` line names
// the commented file in one of two shapes — `` `<path>` (line <N>)`` for a
// line-anchored comment, or a bare ``<path>`` for a file-level one (no
// line). We match both; if that builder's wording changes, update this
// in lockstep.
const COMMENT_RUN_HEADER = 'Operator-added review comment from the kato diff tab.';
// Group 1: backticked path. Group 2: its ``(line N)``. Group 3: bare path
// (file-level comment). The backticked alternative is tried first.
const FILE_LINE_RE = /^File:\s*(?:`([^`]+)`(?:\s*\(line\s*(\d+)\))?|(.+))$/m;
// The builder's placeholder when a comment has no file at all — not a path.
const NO_FILE_SENTINEL = '(no file specified)';

// Returns ``{ file, line }`` for a comment-run prompt (``line`` is 0 for a
// file-level comment), or null when the text isn't one (a normal typed
// prompt, an implementation prompt, etc.).
export function parseCommentRunPrompt(text) {
  const str = String(text || '');
  if (!str.startsWith(COMMENT_RUN_HEADER)) { return null; }
  const match = FILE_LINE_RE.exec(str);
  if (!match) { return null; }
  const file = String(match[1] || match[3] || '').trim();
  if (!file || file === NO_FILE_SENTINEL) { return null; }
  const line = match[2] ? Number(match[2]) : 0;
  return { file, line };
}
