import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AdoptTaskModal from './components/AdoptTaskModal.jsx';
import DiffPane from './components/DiffPane.jsx';
import EditorPane from './components/EditorPane.jsx';
import ForgetTaskModal from './components/ForgetTaskModal.jsx';
import Header from './components/Header.jsx';
import Layout from './components/Layout.jsx';
import OrchestratorActivityFeed from './components/OrchestratorActivityFeed.jsx';
import RightPane from './components/RightPane.jsx';
import SafetyBanner from './components/SafetyBanner.jsx';
import SessionDetail from './components/SessionDetail.jsx';
import SettingsDrawer from './components/SettingsDrawer.jsx';
import TabList from './components/TabList.jsx';
import ToastContainer from './components/ToastContainer.jsx';
import { forgetTaskWorkspace, triggerScan } from './api.js';
import { toast } from './stores/toastStore.js';
import { ChatComposerContext } from './contexts/ChatComposerContext.jsx';
import { useNotifications } from './hooks/useNotifications.js';
import { useNotificationRouting } from './hooks/useNotificationRouting.js';
import { useResizable } from './hooks/useResizable.js';
import { useSafetyState } from './hooks/useSafetyState.js';
import { useSessions } from './hooks/useSessions.js';
import { clearTaskStreamCache } from './hooks/useSessionStream.js';
import { useStatusFeed } from './hooks/useStatusFeed.js';
import { useTaskAttention } from './hooks/useTaskAttention.js';
import { useTaskTabShortcuts } from './hooks/useTaskTabShortcuts.js';
import { useToolMemory } from './hooks/useToolMemory.js';
import { CLAUDE_EVENT } from './constants/claudeEvent.js';
import { agentStatusStore } from './stores/agentStatusStore.js';
import { mergePendingPermissionTaskIds } from './utils/sessionAttention.js';

const RIGHT_PANE_DEFAULT_WIDTH = 380;
const RIGHT_PANE_MIN_WIDTH = 220;
const RIGHT_PANE_MAX_WIDTH = 900;
const RIGHT_PANE_STORAGE_KEY = 'kato.rightPaneWidth';
const LEFT_PANE_DEFAULT_WIDTH = 320;
const LEFT_PANE_MIN_WIDTH = 220;
// Generous upper bound — operators routinely widen the Files /
// Changes pane to read long diffs side-by-side with the chat, and
// 700px capped that too early. The grid uses minmax(0, …) so the
// centre/right columns still collapse gracefully at large values.
const LEFT_PANE_MAX_WIDTH = 1200;
const LEFT_PANE_STORAGE_KEY = 'kato.leftPaneWidth';

export default function App() {
  const [activeTaskId, setActiveTaskIdState] = useState('');
  // The chat-composer textarea owns its own value (see
  // ``MessageForm`` for why — it's the per-keystroke perf fix).
  // App talks to it via ``composerRef.current.appendFragment(...)``
  // so file-tree clicks / Cmd+P picks / diff right-click can push
  // text into the composer without re-rendering the whole tree
  // on every keystroke.
  const composerRef = useRef(null);
  const { sessions, refresh } = useSessions();
  const attention = useTaskAttention();
  // Live agent status published by the active task's SessionDetail. Subscribed
  // once here and passed down (via TabList) so the tab dot/badge derive from the
  // same value as the header chip (UNA-2492). Only the active task ever has a
  // live entry; inactive tabs get null and fall back to polled status.
  const [agentStatuses, setAgentStatuses] = useState({});
  useEffect(() => agentStatusStore.subscribe(setAgentStatuses), []);
  // Lifted from SessionDetail so the same recall function powers
  // both the permission modal AND the tab-attention filter. Without
  // this lift, the server's "has_pending_permission" poll would
  // re-mark a tab orange between auto-allow turns even though the
  // modal correctly suppressed itself.
  const toolMemory = useToolMemory();
  // "+ Add task" picker open/closed state — owned by App so the
  // modal sits above the layout (not inside TabList) and can fire
  // a ``refresh()`` of the session list once an adoption succeeds.
  const [addTaskModalOpen, setAddTaskModalOpen] = useState(false);
  const [workspaceVersion, setWorkspaceVersion] = useState(() => ({}));
  // Tracks whether the operator has manually picked a tab. We auto-focus
  // the live task on the *first* event arrival, but only when the operator
  // hasn't expressed a preference — never steal focus mid-investigation.
  const userPickedTabRef = useRef(false);

  // Debounce per-task workspace bumps so a burst of tool_results during a
  // single turn doesn't make Files / Changes blink every 200ms. The
  // refetch happens 1.2s after the last bump request.
  const bumpTimersRef = useRef({});
  useEffect(() => {
    return () => {
      for (const handle of Object.values(bumpTimersRef.current)) {
        window.clearTimeout(handle);
      }
    };
  }, []);
  const bumpWorkspaceVersion = useCallback((taskId) => {
    if (!taskId) { return; }
    const existing = bumpTimersRef.current[taskId];
    if (existing) { window.clearTimeout(existing); }
    bumpTimersRef.current[taskId] = window.setTimeout(() => {
      delete bumpTimersRef.current[taskId];
      setWorkspaceVersion((prev) => ({
        ...prev,
        [taskId]: (prev[taskId] || 0) + 1,
      }));
    }, 1200);
  }, []);

  // Tab switch → MessageForm remounts (it's keyed on
  // ``activeTaskId`` via SessionDetail), so its internal composer
  // state resets without us doing anything here. The old
  // ``setComposerValue('')`` on taskId change is no longer needed.

  const appendToInput = useCallback((fragment) => {
    const composer = composerRef.current;
    if (composer && typeof composer.appendFragment === 'function') {
      composer.appendFragment(fragment);
    }
  }, []);

  // Reconnect the active task's SSE stream when a diff comment
  // immediately triggers a Claude spawn (so the operator sees
  // Claude working without having to click into the chat pane).
  const sessionReconnectRef = useRef(null);
  const handleRegisterReconnect = useCallback((fn) => {
    sessionReconnectRef.current = fn;
  }, []);
  const handleCommentSpawned = useCallback(() => {
    sessionReconnectRef.current?.();
  }, []);
  const handleDiffCommentsChanged = useCallback(() => {
    bumpWorkspaceVersion(activeTaskId);
  }, [activeTaskId, bumpWorkspaceVersion]);

  const setActiveTaskId = useCallback((taskId) => {
    userPickedTabRef.current = true;
    setActiveTaskIdState(taskId);
    attention.clear(taskId);
  }, [attention]);

  // Tab / Shift+Tab step through the task strip at the top (guards
  // against text fields + open dialogs so normal focus tabbing still
  // works there).
  useTaskTabShortcuts({ sessions, activeTaskId, onSelect: setActiveTaskId });

  // The tab "X" no longer forgets immediately — it stages the task
  // for a hard-confirm modal. Forgetting wipes the local clone and
  // is irreversible, so the operator must approve it in
  // ForgetTaskModal before anything is deleted.
  const [forgetCandidate, setForgetCandidate] = useState(null);
  const requestForgetTask = useCallback((taskId) => {
    if (!taskId) { return; }
    const session = sessions.find((s) => s.task_id === taskId)
      || { task_id: taskId };
    setForgetCandidate(session);
  }, [sessions]);
  const cancelForgetTask = useCallback(() => {
    setForgetCandidate(null);
  }, []);

  const doForgetTask = useCallback(async (taskId) => {
    if (!taskId) { return; }
    // Show the operator what's happening — silently failing was the
    // original bug. Backend now returns 500 with a concrete error
    // message when the workspace dir can't be removed (live process
    // file locks, antivirus, etc.).
    const result = await forgetTaskWorkspace(taskId);
    if (!result.ok) {
      // Route the error text through the canonical apiErrorMessage
      // precedence (body.error → result.error → fallback) instead of
      // the previous hand-rolled chain, so this site agrees with every
      // other error toast on which message wins.
      toast.errorFromResult(result, {
        title: `Couldn't forget ${taskId}`,
        fallback: 'unknown error — see kato logs for details',
        durationMs: 12000,
      });
      // Refresh anyway so the operator sees the current state — a
      // partial cleanup (session record gone, workspace dir still
      // there) should still cause the tab to flicker its dot.
      refresh();
      return;
    }
    clearTaskStreamCache(taskId);
    if (activeTaskId === taskId) {
      setActiveTaskIdState('');
      userPickedTabRef.current = false;
    }
    refresh();
    toast.show({
      kind: 'success',
      title: `Forgot ${taskId}`,
      message: 'Workspace clone and Claude session removed.',
    });
  }, [activeTaskId, refresh]);

  const confirmForgetTask = useCallback(() => {
    const taskId = forgetCandidate?.task_id;
    setForgetCandidate(null);
    if (taskId) { doForgetTask(taskId); }
  }, [forgetCandidate, doForgetTask]);

  const [scanPending, setScanPending] = useState(false);
  const handleScanNow = useCallback(async () => {
    setScanPending(true);
    await triggerScan();
    await refresh();
    setScanPending(false);
  }, [refresh]);

  const onTaskClickFromNotification = useCallback((taskId) => {
    setActiveTaskId(taskId);
  }, [setActiveTaskId]);
  const notifications = useNotifications({
    activeTaskId,
    onTaskClick: onTaskClickFromNotification,
  });

  const routing = useNotificationRouting(notifications.notify);

  const handleStatusEntry = useCallback((entry) => {
    routing.onStatusEntry(entry);
  }, [routing]);

  const handlePendingPermissionChange = useCallback((taskId, pending) => {
    if (!taskId) { return; }
    if (pending) {
      attention.mark(taskId);
      return;
    }
    attention.clear(taskId);
  }, [attention]);

  const handleSessionEvent = useCallback((raw, taskId) => {
    routing.onSessionEvent(raw, taskId);
    if (!raw?.type || !taskId) { return; }
    if (raw.type === CLAUDE_EVENT.PERMISSION_REQUEST
        || raw.type === CLAUDE_EVENT.CONTROL_REQUEST) {
      // Skip the attention mark when the operator has already
      // remembered a decision for this tool — the auto-handler in
      // PermissionDecisionContainer will dispatch silently and a
      // tab-orange flash would be misleading. Without this gate,
      // rapid-fire Bash requests (the screenshotted symptom) make
      // the tab strobe orange even though no UI prompt is needed.
      const toolName = String(
        raw.tool_name || raw.tool
        || raw.request?.tool_name || raw.request?.tool || '',
      ).trim();
      const decision = toolName ? toolMemory.recall(toolName) : null;
      if (decision !== 'allow' && decision !== 'deny') {
        attention.mark(taskId);
      }
    } else if (raw.type === CLAUDE_EVENT.PERMISSION_RESPONSE
        || raw.type === CLAUDE_EVENT.RESULT) {
      attention.clear(taskId);
    }
    // Keep the right pane in sync with disk: bump on every tool result
    // (USER messages carrying tool_result payloads) and on turn end so
    // Files + Changes refetch as soon as the agent has touched anything.
    if (raw.type === CLAUDE_EVENT.USER || raw.type === CLAUDE_EVENT.RESULT) {
      bumpWorkspaceVersion(taskId);
    }
    // RESULT also implies the task may have transitioned state on the
    // ticket platform — refresh the session list now instead of waiting
    // up to REFRESH_INTERVAL_MS for the next poll tick.
    if (raw.type === CLAUDE_EVENT.RESULT) {
      refresh();
    }
    // Auto-focus the live task tab when kato starts working — but only if
    // the operator hasn't manually picked a tab yet. Triggered by ASSISTANT
    // events (the agent saying or doing something) rather than history
    // replay or status pings, so we follow real activity, not boot noise.
    if (raw.type === CLAUDE_EVENT.ASSISTANT
        && !userPickedTabRef.current
        && taskId !== activeTaskId) {
      setActiveTaskIdState(taskId);
    }
  }, [routing, attention, bumpWorkspaceVersion, refresh, activeTaskId, toolMemory]);

  const status = useStatusFeed(handleStatusEntry);
  const safetyState = useSafetyState();

  const resizer = useResizable({
    storageKey: RIGHT_PANE_STORAGE_KEY,
    defaultWidth: RIGHT_PANE_DEFAULT_WIDTH,
    minWidth: RIGHT_PANE_MIN_WIDTH,
    maxWidth: RIGHT_PANE_MAX_WIDTH,
    anchor: 'right',
  });
  const leftResizer = useResizable({
    storageKey: LEFT_PANE_STORAGE_KEY,
    defaultWidth: LEFT_PANE_DEFAULT_WIDTH,
    minWidth: LEFT_PANE_MIN_WIDTH,
    maxWidth: LEFT_PANE_MAX_WIDTH,
    anchor: 'left',
  });
  // Operator clicks the "Scanning for…" pill at the top → the
  // centre column (normally the read-only Monaco editor) gets
  // swapped for the live orchestrator activity feed. Clicking
  // again toggles it back. Lives at App so the pill button and
  // the centre cell stay in sync without prop-drilling through
  // every intermediate.
  const [orchestratorOpen, setOrchestratorOpen] = useState(false);
  const toggleOrchestrator = useCallback(() => {
    setOrchestratorOpen((open) => !open);
  }, []);
  // Settings drawer state. Lives at App so the gear button in the
  // Header and the drawer rendered next to the layout share a
  // single boolean — no prop-drilling, no context.
  const [settingsOpen, setSettingsOpen] = useState(false);
  const openSettings = useCallback(() => setSettingsOpen(true), []);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);

  const activeSession = sessions.find((s) => s.task_id === activeTaskId) || null;
  const activeLiveStatus = (activeTaskId && agentStatuses[activeTaskId]) || null;
  const attentionTaskIds = useMemo(() => {
    return mergePendingPermissionTaskIds(
      attention.taskIds, sessions, toolMemory.recall,
    );
  }, [attention.taskIds, sessions, toolMemory.recall]);
  const activeNeedsAttention = !!activeTaskId && attentionTaskIds.has(activeTaskId);
  const activeSessionKey = activeTaskId || '__none__';
  const activeWorkspaceVersion = workspaceVersion[activeTaskId] || 0;
  // Currently-open file for the middle Monaco editor pane. Lifted
  // to App so FilesTab (rendered on the left) and EditorPane
  // (rendered in the centre) can talk through a single source of
  // truth without coupling them directly. Resets when switching
  // tasks so the editor doesn't render a file from the previous
  // task's workspace.
  const [openFile, setOpenFile] = useState(null);
  const [fileTreeFocusTarget, setFileTreeFocusTarget] = useState(null);
  const openFileRequestRef = useRef(0);
  const fileTreeFocusRequestRef = useRef(0);
  useEffect(() => {
    setOpenFile(null);
    setFileTreeFocusTarget(null);
  }, [activeTaskId]);
  const handleOpenFile = useCallback((info) => {
    // ``info`` shape from FilesTab: { absolutePath, relativePath, repoId }.
    // ``repoId`` is required for the comments POST (the backend keys
    // comments by repo + relative path so a comment on
    // ``src/auth.py`` in repo A doesn't collide with the same path
    // in repo B).
    if (!info || !info.absolutePath) {
      setOpenFile(null);
      return;
    }
    // Opening a file must take over the centre column. If the
    // operator had the orchestrator-activity feed open (via the
    // status pill), close it so the file actually shows instead of
    // staying hidden behind the feed.
    setOrchestratorOpen(false);
    openFileRequestRef.current += 1;
    setOpenFile({
      taskId: activeTaskId,
      absolutePath: info.absolutePath,
      relativePath: info.relativePath || info.absolutePath,
      repoId: info.repoId || '',
      openRequestId: openFileRequestRef.current,
      // 'diff' = the round diff button on a changed tree row; the
      // centre column then renders DiffPane instead of EditorPane.
      view: info.view === 'diff' ? 'diff' : 'file',
      // Set when the operator clicked a file's comment badge (not the
      // name): DiffPane scrolls to the file's first comment thread,
      // not just the top of the file section.
      focusComment: !!info.focusComment,
    });
  }, [activeTaskId]);
  const handleFocusFileInTree = useCallback((target) => {
    const relativePath = String(target?.relativePath || target?.absolutePath || '').trim();
    if (!relativePath) { return; }
    fileTreeFocusRequestRef.current += 1;
    setFileTreeFocusTarget({
      repoId: String(target?.repoId || '').trim(),
      relativePath,
      requestId: fileTreeFocusRequestRef.current,
    });
  }, []);
  // Memoize so the context value is reference-stable across App
  // renders. Without this, EVERY ``useChatComposer()`` consumer
  // (FilesTab, ChangesTab via DiffFileWithComments, etc.)
  // re-renders on every App render — including the wasteful ones
  // that fire on tab focus changes / poll ticks.
  const composerContextValue = useMemo(() => ({ appendToInput }), [appendToInput]);
  const layout = (
    <Layout
      rightWidth={resizer.width}
      leftWidth={leftResizer.width}
      top={
        <TabList
          sessions={sessions}
          activeTaskId={activeTaskId}
          attentionTaskIds={attentionTaskIds}
          activeLiveStatus={activeLiveStatus}
          onSelect={setActiveTaskId}
          onForget={requestForgetTask}
          onOpenAddTask={() => setAddTaskModalOpen(true)}
          onScanNow={handleScanNow}
          scanPending={scanPending}
        />
      }
      // New 3-column layout, left → right:
      //   left   Files + Changes tree (fixed-width column)
      //   center Monaco read-only editor (driven by openFile)
      //   right  Chat session (resizable via the existing resizer)
      //
      // ``width`` + ``onResizePointerDown`` deliberately omitted
      // on the files pane — that pair used to size the pane via an
      // inline ``style={{ width }}``, which now lives in the LEFT
      // grid cell whose track is fixed. Leaving it in would let
      // the pane bleed past its cell into the editor column.
      // The resizer keeps driving ``--right-pane-width`` for the
      // chat column on the right.
      left={
        <RightPane
          activeTaskId={activeTaskId}
          workspaceVersion={activeWorkspaceVersion}
          focusFileTarget={fileTreeFocusTarget}
          onOpenFile={handleOpenFile}
          onResizePointerDown={leftResizer.onPointerDown}
        />
      }
      center={
        orchestratorOpen
          ? <OrchestratorActivityFeed history={status.history} onClose={toggleOrchestrator} />
          : openFile?.view === 'diff'
            ? (
                <DiffPane
                  openFile={openFile}
                  workspaceVersion={activeWorkspaceVersion}
                  onCommentSpawned={handleCommentSpawned}
                  onFocusFileInTree={handleFocusFileInTree}
                  onCommentsChanged={handleDiffCommentsChanged}
                />
              )
            : <EditorPane openFile={openFile} onCommentSpawned={handleCommentSpawned} />
      }
      right={
        <SessionDetail
          key={activeSessionKey}
          session={activeSession}
          needsAttention={activeNeedsAttention}
          onActivity={handleSessionEvent}
          onPendingPermissionChange={handlePendingPermissionChange}
          composerRef={composerRef}
          toolMemory={toolMemory}
          onResizePointerDown={resizer.onPointerDown}
          onOpenFile={handleOpenFile}
          onRegisterReconnect={handleRegisterReconnect}
        />
      }
    />
  );

  return (
    <>
      <ToastContainer />
      <SafetyBanner state={safetyState} />
      <Header
        onRefresh={refresh}
        statusLatest={status.latest}
        statusStale={status.stale}
        statusConnected={status.connected}
        onStatusClick={toggleOrchestrator}
        statusActive={orchestratorOpen}
        onOpenSettings={openSettings}
      />
      <SettingsDrawer
        open={settingsOpen}
        onClose={closeSettings}
        notificationProps={{
          enabled: notifications.enabled,
          supported: notifications.supported,
          permission: notifications.permission,
          kindPrefs: notifications.kindPrefs || {},
          onSetKindEnabled: notifications.setKindEnabled,
          onToggle: notifications.toggle,
        }}
      />
      <ChatComposerContext.Provider value={composerContextValue}>
        {layout}
      </ChatComposerContext.Provider>
      {forgetCandidate && (
        <ForgetTaskModal
          session={forgetCandidate}
          onConfirm={confirmForgetTask}
          onCancel={cancelForgetTask}
        />
      )}
      {addTaskModalOpen && (
        <AdoptTaskModal
          alreadyAdoptedIds={new Set(sessions.map((s) => s.task_id))}
          onClose={() => setAddTaskModalOpen(false)}
          onAdopted={(body) => {
            // Refresh the session list so the adopted task's tab
            // appears, then auto-select it so the operator lands
            // on the new chat without an extra click.
            refresh();
            const adoptedId = String(body?.task_id || '').trim();
            if (adoptedId) { setActiveTaskId(adoptedId); }
          }}
        />
      )}
    </>
  );
}
