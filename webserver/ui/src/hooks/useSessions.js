import { useCallback, useState } from 'react';
import { fetchSessionList } from '../api.js';
import { usePolling } from './usePolling.js';

const REFRESH_INTERVAL_MS = 5000;

export function useSessions() {
  const [sessions, setSessions] = useState([]);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchSessionList();
      if (Array.isArray(data)) { setSessions(data); }
    } catch (_) { /* next tick retries */ }
  }, []);

  usePolling(refresh, REFRESH_INTERVAL_MS);

  return { sessions, refresh };
}
