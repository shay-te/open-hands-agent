import { useCallback, useState } from 'react';
import Header from './components/Header.jsx';
import Layout from './components/Layout.jsx';
import RightPane from './components/RightPane.jsx';
import SessionDetail from './components/SessionDetail.jsx';
import StatusBar from './components/StatusBar.jsx';
import TabList from './components/TabList.jsx';
import { useNotifications } from './hooks/useNotifications.js';
import { useResizable } from './hooks/useResizable.js';
import { useSessions } from './hooks/useSessions.js';
import { useStatusFeed } from './hooks/useStatusFeed.js';
import { classifyStatusEntry } from './utils/classifyStatusEntry.js';

const RIGHT_PANE_DEFAULT_WIDTH = 380;
const RIGHT_PANE_MIN_WIDTH = 220;
const RIGHT_PANE_MAX_WIDTH = 900;
const RIGHT_PANE_STORAGE_KEY = 'kato.rightPaneWidth';

// Top of the React tree. Owns global state (active task id) + the
// notification + status feed wiring. Children are pure-ish — they
// receive props/handlers and render. No globals, no side channels.
export default function App() {
  const [activeTaskId, setActiveTaskId] = useState('');
  const { sessions, refresh } = useSessions();

  const onTaskClickFromNotification = useCallback((taskId) => {
    setActiveTaskId(taskId);
  }, []);

  const notifications = useNotifications({
    activeTaskId,
    onTaskClick: onTaskClickFromNotification,
  });

  // Status feed → optional OS notification.
  const handleStatusEntry = useCallback((entry) => {
    const classification = classifyStatusEntry(entry);
    if (classification) {
      notifications.notify(classification);
    }
  }, [notifications]);
  const status = useStatusFeed(handleStatusEntry);

  // Per-session events → optional OS notification (modal-popping
  // permission requests, error-result notifications when tabbed away).
  const handleSessionEvent = useCallback((raw, taskId) => {
    if (!raw?.type) { return; }
    if (raw.type === 'permission_request' || raw.type === 'control_request') {
      notifications.notify({
        title: 'Approval needed',
        body: extractToolName(raw),
        taskId,
        kind: 'attention',
      });
      return;
    }
    if (raw.type === 'result') {
      const ok = !raw.is_error;
      const summary = typeof raw.result === 'string'
        ? raw.result.slice(0, 140)
        : '';
      notifications.notify({
        title: ok ? 'Claude replied' : 'Turn failed',
        body: summary,
        taskId,
        kind: ok ? 'reply' : 'error',
      });
    }
  }, [notifications]);

  const resizer = useResizable({
    storageKey: RIGHT_PANE_STORAGE_KEY,
    defaultWidth: RIGHT_PANE_DEFAULT_WIDTH,
    minWidth: RIGHT_PANE_MIN_WIDTH,
    maxWidth: RIGHT_PANE_MAX_WIDTH,
    anchor: 'right',
  });

  const activeSession = sessions.find((s) => s.task_id === activeTaskId) || null;

  return (
    <>
      <Header
        notificationsEnabled={notifications.enabled}
        notificationsSupported={notifications.supported}
        onToggleNotifications={notifications.toggle}
        onRefresh={refresh}
      />
      <StatusBar
        latest={status.latest}
        history={status.history}
        stale={status.stale}
      />
      <Layout
        rightWidth={resizer.width}
        left={
          <TabList
            sessions={sessions}
            activeTaskId={activeTaskId}
            onSelect={setActiveTaskId}
          />
        }
        center={
          <SessionDetail
            // ``key`` forces a fresh component instance per task — the
            // session-tool-decisions ref + transient bubbles get clean
            // slates on tab change. Cheaper than wiring resets manually.
            key={activeTaskId || '__none__'}
            session={activeSession}
            onActivity={handleSessionEvent}
          />
        }
        right={
          <RightPane
            activeTaskId={activeTaskId}
            width={resizer.width}
            onResizePointerDown={resizer.onPointerDown}
          />
        }
      />
    </>
  );
}

function extractToolName(raw) {
  const nested = (raw && typeof raw.request === 'object' && raw.request) || {};
  return String(
    raw?.tool_name || raw?.tool || nested.tool_name || nested.tool || 'a tool',
  );
}
