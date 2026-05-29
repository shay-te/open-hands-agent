import { useEffect, useState } from 'react';
import GitProvidersSettingsPanel from './GitProvidersSettingsPanel.jsx';
import NotificationsSettingsPanel from './NotificationsSettingsPanel.jsx';
import RepositoriesSettingsPanel from './RepositoriesSettingsPanel.jsx';
import RepositoryApprovalsSettingsPanel from './RepositoryApprovalsSettingsPanel.jsx';
import SchemaSettingsPanel from './SchemaSettingsPanel.jsx';
import TaskProviderSettingsPanel from './TaskProviderSettingsPanel.jsx';
import { fetchAllSettings } from '../api.js';
import { useEscapeKey } from '../hooks/useEscapeKey.js';
import { cx } from '../utils/cx.js';

// Right-side drawer hosting every operator-editable setting under
// tabs. Five tabs have bespoke logic (provider switchers, the
// approvals table, repo-root path validation, notification
// toggles); the rest are DATA-DRIVEN — one tab per section of the
// ``/api/all-settings`` schema, rendered by the generic
// SchemaSettingsPanel. Adding a new env setting = one entry in
// kato_settings_schema.py, no UI change.

const TAB_REPOS = 'repositories';
const TAB_APPROVALS = 'approvals';
const TAB_TASK_PROVIDER = 'task-provider';
const TAB_GIT_PROVIDER = 'git-provider';
const TAB_NOTIFICATIONS = 'notifications';

// Bespoke (non-schema) tabs, in display order.
const BESPOKE_TABS = [
  { id: TAB_REPOS, label: 'Repositories' },
  { id: TAB_APPROVALS, label: 'Approvals' },
  { id: TAB_TASK_PROVIDER, label: 'Task provider' },
  { id: TAB_GIT_PROVIDER, label: 'Git provider' },
  { id: TAB_NOTIFICATIONS, label: 'Notifications' },
];

export default function SettingsDrawer({
  open,
  onClose,
  notificationProps,
}) {
  const [tab, setTab] = useState(TAB_REPOS);
  // Schema section descriptors {id,label} for the data-driven tabs.
  // Fetched once when the drawer first opens.
  const [schemaTabs, setSchemaTabs] = useState([]);
  const [schemaLoaded, setSchemaLoaded] = useState(false);

  useEffect(() => {
    if (!open || schemaLoaded) { return; }
    let cancelled = false;
    fetchAllSettings().then((result) => {
      if (cancelled) { return; }
      const sections = Array.isArray(result.body?.sections)
        ? result.body.sections
        : [];
      setSchemaTabs(sections.map((s) => ({
        id: `schema:${s.id}`,
        sectionId: s.id,
        label: s.label,
      })));
      setSchemaLoaded(true);
    });
    return () => { cancelled = true; };
  }, [open, schemaLoaded]);

  // ESC closes the drawer. Bound only while open so other ESC
  // consumers (chat search, modals) aren't double-fired.
  useEscapeKey(onClose, open);

  const drawerClass = cx('settings-drawer', open ? 'is-open' : '');
  const backdropClass = cx('settings-drawer-backdrop', open ? 'is-open' : '');

  let panel;
  if (tab === TAB_REPOS) {
    panel = <RepositoriesSettingsPanel />;
  } else if (tab === TAB_APPROVALS) {
    panel = <RepositoryApprovalsSettingsPanel />;
  } else if (tab === TAB_TASK_PROVIDER) {
    panel = <TaskProviderSettingsPanel />;
  } else if (tab === TAB_GIT_PROVIDER) {
    panel = <GitProvidersSettingsPanel />;
  } else if (tab === TAB_NOTIFICATIONS) {
    panel = <NotificationsSettingsPanel {...(notificationProps || {})} />;
  } else if (tab.startsWith('schema:')) {
    const sectionId = tab.slice('schema:'.length);
    panel = <SchemaSettingsPanel key={sectionId} sectionId={sectionId} />;
  }

  const allTabs = [...BESPOKE_TABS, ...schemaTabs];

  return (
    <>
      <div
        className={backdropClass}
        onClick={onClose}
        aria-hidden={!open}
      />
      <aside
        className={drawerClass}
        role="dialog"
        aria-label="Settings"
        aria-hidden={!open}
      >
        <header className="settings-drawer-head">
          <h2>Settings</h2>
          <button
            type="button"
            className="settings-drawer-close"
            onClick={onClose}
            aria-label="Close settings"
            title="Close (Esc)"
          >
            ×
          </button>
        </header>
        <nav className="settings-drawer-tabs" role="tablist">
          {allTabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`settings-drawer-tab ${tab === t.id ? 'is-active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <div className="settings-drawer-body">
          {panel}
        </div>
      </aside>
    </>
  );
}
