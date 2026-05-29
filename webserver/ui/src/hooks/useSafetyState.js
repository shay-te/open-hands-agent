import { useState } from 'react';
import { fetchSafetyState } from '../api.js';
import { usePolling } from './usePolling.js';

const REFRESH_INTERVAL_MS = 30_000;

export function useSafetyState() {
  const [state, setState] = useState(null);

  usePolling(async () => {
    try {
      setState(await fetchSafetyState());
    } catch (_) {
      // The banner is a defensive surface; silently retry on the next tick
      // rather than swallowing the rest of the UI.
    }
  }, REFRESH_INTERVAL_MS);

  return state;
}
