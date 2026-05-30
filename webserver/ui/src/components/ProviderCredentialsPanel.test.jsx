// Tests for the merged ProviderCredentialsPanel via its two thin config
// wrappers (Task provider + Git provider). Covers the shared machine —
// useSettingsResource (load → fields), providerFields (secret masking),
// settingsSource (badges), useRestartingSave (save → toast + restart
// banner) — plus the one real difference: Task writes the *active*
// provider, Git does not (includeActive).
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchTaskProviders: vi.fn(),
  updateTaskProvider: vi.fn(),
  fetchGitProviders: vi.fn(),
  updateGitProvider: vi.fn(),
}));
vi.mock('../stores/toastStore.js', () => ({
  toast: { show: vi.fn(), errorFromResult: vi.fn() },
}));

import TaskProviderSettingsPanel from './TaskProviderSettingsPanel.jsx';
import GitProvidersSettingsPanel from './GitProvidersSettingsPanel.jsx';
import {
  fetchTaskProviders, updateTaskProvider,
  fetchGitProviders, updateGitProvider,
} from '../api.js';
import { toast } from '../stores/toastStore.js';

const TASK_BODY = {
  active: 'youtrack',
  supported: ['youtrack', 'jira'],
  providers: {
    youtrack: {
      fields: {
        base_url: { value: 'https://yt.example', source: 'kato_settings' },
        token: { value: 'secret-tok', source: 'env' },
      },
    },
    jira: { fields: { base_url: { value: '', source: 'unset' } } },
  },
  settings_file_path: '~/.kato/settings.json',
};

const GIT_BODY = {
  supported: ['bitbucket', 'github'],
  providers: {
    bitbucket: {
      fields: {
        username: { value: 'me', source: 'env_file' },
        app_password: { value: 'pw', source: 'kato_settings' },
      },
    },
    github: { fields: { token: { value: '', source: 'unset' } } },
  },
  settings_file_path: '~/.kato/settings.json',
};

beforeEach(() => {
  fetchTaskProviders.mockReset();
  updateTaskProvider.mockReset();
  fetchGitProviders.mockReset();
  updateGitProvider.mockReset();
  toast.show.mockReset();
});

describe('ProviderCredentialsPanel — Task provider (includeActive)', () => {
  test('loads, renders fields + source badges, masks secret fields', async () => {
    fetchTaskProviders.mockResolvedValue({ ok: true, body: TASK_BODY });
    render(<TaskProviderSettingsPanel />);

    await waitFor(() => expect(screen.getByText('base_url')).toBeInTheDocument());
    expect(screen.getByText('token')).toBeInTheDocument();
    expect(screen.getByText('Active provider')).toBeInTheDocument();
    // sourceLabel: kato_settings -> "saved", env -> "live"
    expect(screen.getByText('saved')).toBeInTheDocument();
    expect(screen.getByText('live')).toBeInTheDocument();
    // isSecretKey('token') -> password input
    expect(screen.getByDisplayValue('secret-tok')).toHaveAttribute('type', 'password');
    expect(screen.getByDisplayValue('https://yt.example')).toHaveAttribute('type', 'text');
  });

  test('edit enables Save; Save sends {active, provider, fields} + shows restart banner', async () => {
    fetchTaskProviders.mockResolvedValue({ ok: true, body: TASK_BODY });
    updateTaskProvider.mockResolvedValue({ ok: true, body: {} });
    render(<TaskProviderSettingsPanel />);
    await waitFor(() => expect(screen.getByText('base_url')).toBeInTheDocument());

    const saveBtn = screen.getByRole('button', { name: /^Save$/i });
    expect(saveBtn).toBeDisabled();

    const baseUrlInput = screen.getByDisplayValue('https://yt.example');
    // The panel re-seeds ``draft`` from the server values in a
    // [selected, meta.providers] effect that can still be PENDING right
    // after the load settles; under parallel load it may run *after*
    // this edit and wipe it (isDirty flips back to false → Save stays
    // disabled). Re-fire the change inside waitFor so the edit re-applies
    // until the re-seed has settled and the dirty diff sticks.
    await waitFor(() => {
      fireEvent.change(baseUrlInput, { target: { value: 'https://new.example' } });
      expect(saveBtn).not.toBeDisabled();
    });

    fireEvent.click(saveBtn);
    await waitFor(() => expect(updateTaskProvider).toHaveBeenCalledTimes(1));
    const arg = updateTaskProvider.mock.calls[0][0];
    expect(arg).toMatchObject({ active: 'youtrack', provider: 'youtrack' });
    expect(arg.fields.base_url).toBe('https://new.example');
    await waitFor(() => expect(screen.getByText(/Restart kato/i)).toBeInTheDocument());
  });
});

describe('ProviderCredentialsPanel — Git provider (no active)', () => {
  test('uses "Host" label and Save sends {provider, fields} WITHOUT active', async () => {
    fetchGitProviders.mockResolvedValue({ ok: true, body: GIT_BODY });
    updateGitProvider.mockResolvedValue({ ok: true, body: {} });
    render(<GitProvidersSettingsPanel />);
    await waitFor(() => expect(screen.getByText('username')).toBeInTheDocument());

    expect(screen.getByText('Host')).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue('me'), { target: { value: 'newuser' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/i }));

    await waitFor(() => expect(updateGitProvider).toHaveBeenCalledTimes(1));
    const arg = updateGitProvider.mock.calls[0][0];
    expect(arg).not.toHaveProperty('active');
    expect(arg).toMatchObject({ provider: 'bitbucket' });
    expect(arg.fields.username).toBe('newuser');
  });

  test('load error renders the error message', async () => {
    fetchGitProviders.mockResolvedValue({ ok: false, error: 'kaboom' });
    render(<GitProvidersSettingsPanel />);
    await waitFor(() => expect(screen.getByText('kaboom')).toBeInTheDocument());
  });
});
