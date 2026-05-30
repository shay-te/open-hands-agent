import { useCallback, useEffect, useRef, useState } from 'react';

// Per-session option selector state, shared by the model picker and
// the effort picker in SessionDetail. Both pickers follow the exact
// same shape:
//
//   - fetch the option LIST once (loadedRef-guarded so it never
//     re-fetches per task switch — the catalogue is global),
//   - fetch the CURRENT value whenever the bound task changes,
//     resetting to '' when no task is bound,
//   - on change, optimistically set local state then POST the choice.
//
// The two differ only in the API functions and the result keys
// (``models`` vs ``levels`` for the list, ``model`` vs ``effort`` for
// the current value), so those are passed in as config.
//
// Returns ``[options, selected, onChange]``.
export function useSessionOption(taskId, {
  fetchOptions,
  optionsKey,
  fetchCurrent,
  currentKey,
  setCurrent,
}) {
  const [options, setOptions] = useState([]);
  const [selected, setSelected] = useState('');
  const loadedRef = useRef(false);

  // Fetch the option catalogue once. Guarded so tab switches don't
  // re-hit the endpoint — the list is the same for every task.
  useEffect(() => {
    if (loadedRef.current) { return; }
    loadedRef.current = true;
    fetchOptions().then((result) => {
      if (result && Array.isArray(result[optionsKey])) {
        setOptions(result[optionsKey]);
      }
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load the current value for the bound task; reset to '' with no task.
  useEffect(() => {
    if (!taskId) { setSelected(''); return; }
    fetchCurrent(taskId).then((result) => {
      setSelected((result && result[currentKey]) || '');
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  // Optimistically reflect the choice, then persist it.
  const onChange = useCallback(async (value) => {
    setSelected(value);
    await setCurrent(taskId, value);
  }, [taskId, setCurrent]);

  return [options, selected, onChange];
}
