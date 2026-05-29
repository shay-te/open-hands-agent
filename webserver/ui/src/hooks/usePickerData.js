import { useEffect, useRef, useState } from 'react';

// The cancelled-flag fetch lifecycle shared by the data-loading picker
// modals. ``fetcher`` is an async fn that resolves to the data to show
// (e.g. a list) or throws to signal failure. The hook owns
// loading/error/data plus a ``cancelled`` guard so a late resolve after
// the modal closes/unmounts is a no-op. ``deps`` restart the fetch when
// they change (e.g. a search ``query`` the server filters on). ``initial``
// is the data value before the first load (and after an error) — pass
// ``[]`` for list modals so callers can ``.map`` safely. Returns
// { data, loading, error }.
export function usePickerData(fetcher, deps = [], initial = null) {
  const [data, setData] = useState(initial);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    Promise.resolve()
      .then(() => fetcherRef.current())
      .then((result) => { if (!cancelled) { setData(result); } })
      .catch((err) => {
        if (!cancelled) {
          setError(String(err?.message || err) || 'request failed');
          setData(initial);
        }
      })
      .finally(() => { if (!cancelled) { setLoading(false); } });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, loading, error };
}
