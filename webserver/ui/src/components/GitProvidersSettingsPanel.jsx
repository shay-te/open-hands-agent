import { fetchGitProviders, updateGitProvider } from '../api.js';
import ProviderCredentialsPanel from './ProviderCredentialsPanel.jsx';

// "Git provider" tab — credentials kato uses to clone / push / open PRs.
// Thin config over the shared <ProviderCredentialsPanel>. No active
// selector: kato infers the host from each repo's remote URL, so this
// tab only edits per-host creds (includeActive defaults off).
const HOST_LABELS = {
  bitbucket: 'Bitbucket',
  github: 'GitHub',
  gitlab: 'GitLab',
};

export default function GitProvidersSettingsPanel() {
  return (
    <ProviderCredentialsPanel
      fetchFn={fetchGitProviders}
      updateFn={updateGitProvider}
      labels={HOST_LABELS}
      defaultProvider="bitbucket"
      title="Git provider"
      loadingMessage="Loading git hosts…"
      selectLabel="Host"
      selectHint="Picking a host here only chooses which creds to edit — it does NOT change which platform kato polls (that's the Task provider tab)."
      description={(
        <>
          Credentials kato uses to clone, push branches, and open PRs.
          Kato picks the host automatically from each repo's remote
          URL — this just sets the creds per host. Saved to
        </>
      )}
    />
  );
}
