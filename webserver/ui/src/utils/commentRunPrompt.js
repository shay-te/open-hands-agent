// Detect a kato comment-run prompt and pull out the file it targets, so
// the chat's sticky "You asked" prompt can offer a jump-to-comment icon.
//
// kato builds these prompts in ``AgentService._comment_agent_prompt``
// (Python): the first line is a fixed header, and a ``File: `<path>`
// (line <N>)`` line names the commented file. We match that stable shape
// — if that builder's wording changes, update this in lockstep.
const COMMENT_RUN_HEADER = 'Operator-added review comment from the kato diff tab.';
const FILE_LINE_RE = /^File:\s*`([^`]+)`(?:\s*\(line\s*(\d+)\))?/m;

// Returns ``{ file, line }`` for a comment-run prompt, or null when the
// text isn't one (a normal typed prompt, an implementation prompt, etc.).
export function parseCommentRunPrompt(text) {
  const str = String(text || '');
  if (!str.startsWith(COMMENT_RUN_HEADER)) { return null; }
  const match = FILE_LINE_RE.exec(str);
  if (!match) { return null; }
  const file = String(match[1] || '').trim();
  if (!file) { return null; }
  const line = match[2] ? Number(match[2]) : 0;
  return { file, line };
}
