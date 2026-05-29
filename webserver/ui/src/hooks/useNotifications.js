import { useCallback, useEffect, useRef, useState } from 'react';
import {
  readEnabled,
  readKindPrefs,
  writeEnabled,
  writeKindPrefs,
} from '../utils/notificationsStorage.js';

export function useNotifications({ activeTaskId, onTaskClick }) {
  const supported = typeof window !== 'undefined' && 'Notification' in window;
  const [permission, setPermission] = useState(
    supported ? Notification.permission : 'denied',
  );
  const [enabled, setEnabled] = useState(() => (
    supported && permission === 'granted' && readEnabled()
  ));
  const [kindPrefs, setKindPrefs] = useState(() => readKindPrefs());
  const onTaskClickRef = useRef(onTaskClick);
  onTaskClickRef.current = onTaskClick;
  const activeTaskIdRef = useRef(activeTaskId);
  activeTaskIdRef.current = activeTaskId;
  const kindPrefsRef = useRef(kindPrefs);
  kindPrefsRef.current = kindPrefs;

  const persistEnabled = useCallback((value) => {
    setEnabled(value);
    writeEnabled(value);
  }, []);

  const setKindEnabled = useCallback((kind, on) => {
    setKindPrefs((prev) => {
      const next = { ...prev, [kind]: !!on };
      writeKindPrefs(next);
      return next;
    });
  }, []);

  const toggle = useCallback(async () => {
    if (!supported) { return; }
    if (enabled) { persistEnabled(false); return; }
    if (Notification.permission === 'denied') { return; }
    if (Notification.permission === 'default') {
      const result = await Notification.requestPermission();
      setPermission(result);
      if (result !== 'granted') { return; }
    }
    persistEnabled(true);
  }, [enabled, persistEnabled, supported]);

  const notify = useCallback(({ title, body, taskId, kind }) => {
    if (!enabled || !supported || Notification.permission !== 'granted') { return; }
    if (!document.hidden && taskId && taskId === activeTaskIdRef.current) { return; }
    // Per-kind opt-out. Unknown kinds are allowed by default so a new
    // notification surface doesn't get silently swallowed.
    const kindKey = kind || 'info';
    if (kindPrefsRef.current[kindKey] === false) { return; }
    try {
      const notification = new Notification(title, {
        body: body || '',
        icon: '/logo.png',
        tag: `kato-${kindKey}-${taskId || 'global'}`,
      });
      notification.onclick = () => {
        window.focus();
        if (taskId && typeof onTaskClickRef.current === 'function') {
          onTaskClickRef.current(taskId);
        }
        notification.close();
      };
    } catch (_) { /* stricter browser policies — degrade silently */ }
  }, [enabled, supported]);

  useEffect(() => {
    if (!supported) { return; }
    const id = setInterval(() => {
      if (Notification.permission !== permission) {
        setPermission(Notification.permission);
        if (Notification.permission !== 'granted' && enabled) {
          persistEnabled(false);
        }
      }
    }, 5000);
    return () => clearInterval(id);
  }, [enabled, permission, persistEnabled, supported]);

  return {
    supported,
    enabled,
    permission,
    toggle,
    notify,
    kindPrefs,
    setKindEnabled,
  };
}
