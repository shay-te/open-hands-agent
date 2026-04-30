// One row in the tab list. Status dot + task id + summary.
export default function Tab({ session, active, onSelect }) {
  const status = session.status || 'active';
  return (
    <li
      className={`tab ${active ? 'active' : ''}`.trim()}
      data-task-id={session.task_id}
      onClick={() => onSelect(session.task_id)}
    >
      <span
        className={`status-dot status-${status}`}
        title={status}
      />
      <strong>{session.task_id}</strong>
      <p>{session.task_summary || ''}</p>
    </li>
  );
}
