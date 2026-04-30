import Tab from './Tab.jsx';

// Left pane: list of session tabs. Pure render — selection state lives
// upstream so the chat detail can react to the same changes.
export default function TabList({ sessions, activeTaskId, onSelect }) {
  if (!sessions || sessions.length === 0) {
    return (
      <aside id="tabs-pane">
        <p id="empty-state" className="empty">
          No active planning sessions yet. Tag a task with{' '}
          <code>kato:wait-planning</code> and run kato — a tab will appear here.
        </p>
      </aside>
    );
  }
  return (
    <aside id="tabs-pane">
      <ul id="tab-list">
        {sessions.map((session) => (
          <Tab
            key={session.task_id}
            session={session}
            active={session.task_id === activeTaskId}
            onSelect={onSelect}
          />
        ))}
      </ul>
    </aside>
  );
}
