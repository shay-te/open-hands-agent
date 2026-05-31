import { useState } from 'react';
import { fetchTaskComments } from '../api.js';
import { buildCommentStatusByLocation } from '../utils/commentStatus.js';
import { usePolling } from './usePolling.js';

const EMPTY_STATUS_MAP = new Map();

// Polls a task's diff comments into a Map(commentStatusKey ->
// kato_status), so the chat's comment-run sticky prompt can tint its
// jump icon by the live status of the comment kato is addressing.
//
// Gated by ``enabled`` — the chat only turns it on while a comment-run
// prompt is actually on screen, so an ordinary transcript issues no
// extra requests. The fetched map is stamped with the task it came from
// and only returned when that still matches ``taskId``; on a task switch
// the previous task's statuses are dropped immediately (returns empty)
// rather than briefly mis-tinting until the next fetch lands.
export function useCommentStatusMap(taskId, enabled = true) {
  const [state, setState] = useState({ taskId: null, map: EMPTY_STATUS_MAP });
  const active = !!(taskId && enabled);
  usePolling(async () => {
    if (!active) { return; }
    const res = await fetchTaskComments(taskId);
    if (!res || !res.ok) { return; }
    setState({ taskId, map: buildCommentStatusByLocation(res.body?.comments) });
  }, 5000, [taskId, active], { enabled: active });
  return active && state.taskId === taskId ? state.map : EMPTY_STATUS_MAP;
}
