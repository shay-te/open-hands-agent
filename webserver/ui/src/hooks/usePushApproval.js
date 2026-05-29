import { useEffect, useState } from 'react';
import { approveTaskPush, fetchAwaitingPushApproval } from '../api.js';
import { useBusyAction } from './useBusyAction.js';
import { usePolling } from './usePolling.js';

const POLL_INTERVAL_MS = 5000;

export function usePushApproval(taskId) {
  const [awaiting, setAwaiting] = useState(false);

  // The poll below is disabled when there's no task; reset the flag so a
  // stale "awaiting" doesn't linger after the task clears.
  useEffect(() => {
    if (!taskId) { setAwaiting(false); }
  }, [taskId]);

  usePolling(async () => {
    try {
      const body = await fetchAwaitingPushApproval(taskId);
      setAwaiting(!!body?.awaiting_push_approval);
    } catch (_) {
      // Best-effort; UI keeps last known state.
    }
  }, POLL_INTERVAL_MS, [taskId], { enabled: !!taskId });

  const [busy, approve] = useBusyAction(
    () => approveTaskPush(taskId),
    { enabled: !!taskId, onDone: (result) => { if (result.ok) { setAwaiting(false); } } },
  );

  return { awaiting, busy, approve };
}
