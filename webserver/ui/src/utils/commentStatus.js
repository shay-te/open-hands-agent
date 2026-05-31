// Shared kato_status model for diff review comments. Two UI surfaces
// tint an element by a comment's ``kato_status`` and must agree on the
// rules: the Files tree badge (``buildFilesCommentMeta``) and the
// chat's comment-run sticky-prompt jump icon (``EventLog``). Keeping the
// precedence + the location key here is what stops the two from drifting.

// Most-urgent first: a file/location with any failed thread tints red,
// else queued, etc. Unknown/empty statuses rank last. Module-private —
// callers use moreUrgentCommentStatus, not the raw ordering.
const COMMENT_STATUS_PRECEDENCE = ['failed', 'queued', 'in_progress', 'addressed'];

export function moreUrgentCommentStatus(a, b) {
  const rank = (status) => {
    const index = COMMENT_STATUS_PRECEDENCE.indexOf(status);
    return index === -1 ? COMMENT_STATUS_PRECEDENCE.length : index;
  };
  return rank(b) < rank(a) ? b : a;
}

// Key tying a comment-run prompt (which only names file + anchor line)
// back to the comment it targets. File path + line locate a root thread
// uniquely in practice. Both sides — the map builder below and the chat
// lookup — must run their inputs through this so the keys match. Any
// non-positive line (file-level comments are stored as -1 by the
// backend; the prompt omits the line entirely) collapses to 0, so the
// two sides agree on "no specific line".
export function commentStatusKey(file, line) {
  const n = Number(line);
  return `${String(file || '').trim()}::${n > 0 ? n : 0}`;
}

// Map(commentStatusKey -> kato_status) over a task's ROOT comments, so a
// comment-run prompt can look up the live status of the exact comment it
// targets. Replies (``parent_id`` set) carry no run status of their own;
// blank statuses are skipped. On the rare two-roots-same-line collision
// the more-urgent status wins.
export function buildCommentStatusByLocation(comments) {
  const byLocation = new Map();
  for (const comment of comments || []) {
    if (String(comment?.parent_id || '')) { continue; }
    const filePath = String(comment?.file_path || '').trim();
    if (!filePath) { continue; }
    const status = String(comment?.kato_status || '').trim();
    if (!status) { continue; }
    const key = commentStatusKey(filePath, comment?.line);
    const prev = byLocation.get(key);
    byLocation.set(key, prev ? moreUrgentCommentStatus(prev, status) : status);
  }
  return byLocation;
}
