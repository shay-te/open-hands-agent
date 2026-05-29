import { useCallback, useEffect, useRef, useState } from 'react';
import { apiErrorMessage } from '../utils/apiError.js';

// The fetch / loading / error / refresh state machine shared by the
// settings panels. ``fetchFn`` resolves to the API client's
// ``{ ok, body, error }``. On success ``onLoaded(body)`` lets the panel
// seed its own derived state (the supported list, the draft, etc.) —
// the hook intentionally does NOT own that, since each panel shapes it
// differently. On failure the error message is exposed. Runs once on
// mount; ``refresh`` is stable (``fetchFn``/``onLoaded`` read via refs).
// Returns { loading, error, refresh, setError }.
export function useSettingsResource(fetchFn, onLoaded) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const fetchRef = useRef(fetchFn);
  fetchRef.current = fetchFn;
  const onLoadedRef = useRef(onLoaded);
  onLoadedRef.current = onLoaded;

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    const result = await fetchRef.current();
    if (!result.ok) {
      setError(apiErrorMessage(result, 'load failed'));
      setLoading(false);
      return result;
    }
    if (onLoadedRef.current) { onLoadedRef.current(result.body || {}); }
    setLoading(false);
    return result;
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return { loading, error, refresh, setError };
}
