import { useEffect, useState } from 'react';
import FilesTab from '../FilesTab.jsx';
import LeftPaneResizer from './LeftPaneResizer.jsx';

export default function RightPane({
  activeTaskId,
  workspaceVersion = 0,
  focusFileTarget = null,
  width,
  onResizePointerDown,
  onOpenFile,
}) {
  // Bumped each time Cmd/Ctrl+P fires. ``FilesTab`` watches this and
  // focuses its filter input on every increment. Using a counter
  // (vs a boolean toggle) so consecutive invocations always trigger
  // the focus effect — repeated Cmd+P presses re-focus the input
  // even if it's already mounted.
  const [focusFilterSignal, setFocusFilterSignal] = useState(0);

  // VS Code's Cmd+P / Ctrl+P focuses the file search box. Only
  // intercepted when there's an active task — otherwise the browser
  // keeps its default Print binding so operators on the empty-state
  // screen aren't surprised. (There used to be a Files/Changes tab
  // toggle here; the Changes tab was removed, so the file tree is
  // now the only view and Cmd+P just focuses its filter.)
  useEffect(() => {
    if (!activeTaskId) { return; }
    function onKeyDown(event) {
      const isP = event.key === 'p' || event.key === 'P';
      if (!isP) { return; }
      const meta = event.metaKey || event.ctrlKey;
      if (!meta) { return; }
      if (event.shiftKey || event.altKey) { return; }
      event.preventDefault();
      setFocusFilterSignal((tick) => tick + 1);
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [activeTaskId]);

  const sessionBody = activeTaskId ? (
    <div className="right-pane">
      <div className="right-pane-body">
        <FilesTab
          taskId={activeTaskId}
          workspaceVersion={workspaceVersion}
          focusFilterSignal={focusFilterSignal}
          focusFileTarget={focusFileTarget}
          onOpenFile={onOpenFile}
        />
      </div>
    </div>
  ) : (
    <div className="left-pane-empty">
      <p className="left-pane-empty-title">No task selected</p>
      <p className="left-pane-empty-hint">
        Pick a task from the strip at the top to see its files and changes here.
      </p>
      <p className="left-pane-empty-hint">
        Click the status pill (top of the window) to watch the orchestrator's
        live activity feed.
      </p>
    </div>
  );

  // ``width`` is forwarded as an inline style ONLY when an
  // explicit value was passed. In the new top-tabs layout the
  // pane lives in a fixed-width grid cell and must not set
  // ``width``: setting it (even to ``undefined``) used to render
  // ``style="width: undefined"`` which most browsers ignore but
  // some tools normalise to ``auto``, plus the previous prop
  // default ``width={resizer.width}`` made the pane overflow its
  // cell. Letting the grid track decide the width is the right
  // default; only the legacy resizable-sidebar layout opts in.
  const inlineStyle = (width !== undefined && width !== null && width !== '')
    ? { width }
    : undefined;
  // In the new top-tabs layout the Files/Changes panel is the LEFT
  // column — its resizer lives on the RIGHT edge so dragging right
  // grows the panel into the editor's space. The legacy sidebar
  // layout still uses the right-edge resizer here too (the visual
  // result is the same: a draggable boundary), so a single
  // ``LeftPaneResizer`` covers both.
  const resizer = typeof onResizePointerDown === 'function'
    ? <LeftPaneResizer onPointerDown={onResizePointerDown} />
    : null;
  return (
    <aside id="right-pane" style={inlineStyle}>
      <div id="right-pane-root">
        {sessionBody}
      </div>
      {resizer}
    </aside>
  );
}
