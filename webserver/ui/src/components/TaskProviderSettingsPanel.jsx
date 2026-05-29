import { fetchTaskProviders, updateTaskProvider } from '../api.js';
import ProviderCredentialsPanel from './ProviderCredentialsPanel.jsx';

// "Task provider" tab — where tickets live + which platform kato polls.
// Thin config over the shared <ProviderCredentialsPanel>; ``includeActive``
// is on because this tab also writes KATO_ISSUE_PLATFORM (the active
// provider), unlike the git tab which only edits per-host creds.
const PROVIDER_LABELS = {
  youtrack: 'YouTrack',
  jira: 'Jira',
  github: 'GitHub Issues',
  gitlab: 'GitLab Issues',
  bitbucket: 'Bitbucket Issues',
};

export default function TaskProviderSettingsPanel() {
  return (
    <ProviderCredentialsPanel
      fetchFn={fetchTaskProviders}
      updateFn={updateTaskProvider}
      labels={PROVIDER_LABELS}
      defaultProvider="youtrack"
      includeActive
      title="Task provider"
      loadingMessage="Loading task providers…"
      selectLabel="Active provider"
      selectHint="The other providers' fields stay editable — switch between them with this dropdown."
      description={(
        <>
          Where tickets live + which platform kato polls for assigned
          work. The dropdown sets <code>KATO_ISSUE_PLATFORM</code>;
          fields are saved to
        </>
      )}
    />
  );
}
