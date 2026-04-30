import { useEffect, useRef, useState } from 'react';
import { safeParseJSON } from '../utils/sse.js';

const STALE_AFTER_MS = 30_000;
const HISTORY_LIMIT = 200;

// Subscribes to /api/status/events and keeps the last N entries.
// `onEntry` is invoked once per unique entry (deduplicated by sequence)
// so callers can route notifications without re-implementing the
// dedup logic.
export function useStatusFeed(onEntry) {
  const [latest, setLatest] = useState(null);
  const [history, setHistory] = useState([]);
  const [stale, setStale] = useState(false);
  const seenSequencesRef = useRef(new Set());
  const onEntryRef = useRef(onEntry);
  onEntryRef.current = onEntry;

  useEffect(() => {
    let staleTimer = null;
    const resetStale = () => {
      setStale(false);
      if (staleTimer) { clearTimeout(staleTimer); }
      staleTimer = setTimeout(() => setStale(true), STALE_AFTER_MS);
    };

    const stream = new EventSource('/api/status/events');
    stream.addEventListener('status_entry', (event) => {
      const entry = safeParseJSON(event.data);
      if (!entry || seenSequencesRef.current.has(entry.sequence)) { return; }
      seenSequencesRef.current.add(entry.sequence);
      setLatest(entry);
      setHistory((prev) => {
        const next = [...prev, entry];
        return next.length > HISTORY_LIMIT
          ? next.slice(-HISTORY_LIMIT)
          : next;
      });
      resetStale();
      if (typeof onEntryRef.current === 'function') {
        onEntryRef.current(entry);
      }
    });
    stream.addEventListener('status_disabled', () => {
      setStale(true);
      stream.close();
    });
    stream.onerror = () => {
      if (stream.readyState === EventSource.CLOSED) {
        setStale(true);
      }
    };
    resetStale();
    return () => {
      stream.close();
      if (staleTimer) { clearTimeout(staleTimer); }
    };
  }, []);

  return { latest, history, stale };
}
