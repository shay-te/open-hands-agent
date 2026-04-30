import { useState } from 'react';
import FilesTab from '../FilesTab.jsx';
import ChangesTab from '../ChangesTab.jsx';
import RightPaneResizer from './RightPaneResizer.jsx';

const TAB_FILES = 'files';
const TAB_CHANGES = 'changes';

// Right-side pane: drag-resizable column hosting the Files / Changes
// tabs. Files + Changes are scoped to the active task; the empty state
// renders when no task is selected.
export default function RightPane({ activeTaskId, width, onResizePointerDown }) {
  const [tab, setTab] = useState(TAB_FILES);

  return (
    <aside id="right-pane" style={{ width }}>
      <RightPaneResizer onPointerDown={onResizePointerDown} />
      <div id="right-pane-root">
        {activeTaskId ? (
          <div className="right-pane">
            <nav className="right-pane-tabs">
              <button
                type="button"
                className={tab === TAB_FILES ? 'active' : ''}
                onClick={() => setTab(TAB_FILES)}
              >
                Files
              </button>
              <button
                type="button"
                className={tab === TAB_CHANGES ? 'active' : ''}
                onClick={() => setTab(TAB_CHANGES)}
              >
                Changes
              </button>
            </nav>
            <div className="right-pane-body">
              {tab === TAB_FILES && <FilesTab taskId={activeTaskId} />}
              {tab === TAB_CHANGES && <ChangesTab taskId={activeTaskId} />}
            </div>
          </div>
        ) : (
          <div className="right-pane-empty">
            Select a tab on the left to inspect files and changes.
          </div>
        )}
      </div>
    </aside>
  );
}
