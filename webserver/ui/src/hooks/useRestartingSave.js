import { useCallback, useRef, useState } from 'react';
import { toast } from '../stores/toastStore.js';

// The save() state machine shared by the settings panels:
//   setSaving(true) → await updateFn(...args) → on !ok toast an error and
//   return → else toast success + stamp savedAt (drives <RestartBanner
//   show={savedAt}/>) → onSaved(result) → finally clear saving.
// Every toast string/duration is overridable so each panel keeps its exact
// wording; ``useServerMessage`` prefers ``result.body.message`` for the
// success toast (the server sometimes tailors the "restart" line).
// ``updateFn``/``onSaved`` are read via refs so ``save`` stays stable.
export function useRestartingSave(updateFn, {
  onSaved,
  errorTitle = 'Save failed',
  errorFallback = 'save failed',
  errorDurationMs = 8000,
  successTitle = 'Saved',
  successMessage = 'Restart kato for the change to take effect.',
  successDurationMs = 7000,
  useServerMessage = true,
} = {}) {
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(null);
  const updateRef = useRef(updateFn);
  updateRef.current = updateFn;
  const onSavedRef = useRef(onSaved);
  onSavedRef.current = onSaved;

  const save = useCallback(async (...args) => {
    setSaving(true);
    try {
      const result = await updateRef.current(...args);
      if (!result.ok) {
        toast.errorFromResult(result, {
          title: errorTitle,
          fallback: errorFallback,
          durationMs: errorDurationMs,
        });
        return result;
      }
      toast.show({
        kind: 'success',
        title: successTitle,
        message: (useServerMessage && result.body?.message) || successMessage,
        durationMs: successDurationMs,
      });
      setSavedAt(Date.now());
      if (onSavedRef.current) { onSavedRef.current(result); }
      return result;
    } finally {
      setSaving(false);
    }
  }, [errorTitle, errorFallback, errorDurationMs, successTitle, successMessage, successDurationMs, useServerMessage]);

  return { saving, savedAt, save };
}
