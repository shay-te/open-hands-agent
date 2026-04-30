import { useCallback, useEffect, useState } from 'react';
import { fetchSessionList } from '../api.js';

const REFRESH_INTERVAL_MS = 5000;

// Polls /api/sessions on a 5s tick and on demand. Returns the snapshot
// plus a `refresh()` callback so any UI surface (refresh button, post-
// action triggers) can ask for an immediate update without waiting for
// the timer.
export function useSessions() {
  const [sessions, setSessions] = useState([]);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchSessionList();
      if (Array.isArray(data)) { setSessions(data); }
    } catch (_) {
      // Transient network failures are fine — the next tick retries.
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  return { sessions, refresh };
}
