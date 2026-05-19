"""Declarative schema for every operator-editable env setting.

One source of truth the planning-UI Settings drawer renders from.
Each section becomes a tab; each field knows its type, help text,
and (for the dangerous ones) warnings. The webserver's
``/api/all-settings`` route serves this schema + the resolved
values and accepts writes for any key whose name appears here — the
schema IS the write whitelist, so a payload can't smuggle a key
the UI doesn't declare.

Field ``type``:
  * ``text``    — single-line string
  * ``secret``  — string rendered as a password input
  * ``number``  — integer / float (stored as a string in .env land)
  * ``bool``    — ``"true"`` / ``"false"``
  * ``select``  — one of ``options``

Provider + repo-root keys are deliberately NOT here — they have
dedicated tabs (Task provider / Git provider / Repositories) with
custom logic (active-platform switch, path validation). Everything
else in ``.env.example`` is covered below.
"""

from __future__ import annotations

import math

LOG_LEVELS = ['debug', 'info', 'warning', 'error', 'critical']

# Keys whose value must be a valid http/https URL when non-empty.
# Covers both schema fields and provider/git-host fields that live
# outside the schema (task-providers, git-providers tabs).
_URL_KEYS: frozenset[str] = frozenset({
    'OPENHANDS_BASE_URL',
    'OPENHANDS_TESTING_BASE_URL',
    'OPENHANDS_WEB_URL',
    'OPENHANDS_LLM_BASE_URL',
    'YOUTRACK_API_BASE_URL',
    'JIRA_API_BASE_URL',
    'GITHUB_API_BASE_URL',
    'GITLAB_API_BASE_URL',
    'BITBUCKET_API_BASE_URL',
})

# Keys whose value must look like an email address when non-empty.
_EMAIL_KEYS: frozenset[str] = frozenset({
    'KATO_FAILURE_EMAIL_TO',
    'KATO_FAILURE_EMAIL_SENDER_EMAIL',
    'KATO_COMPLETION_EMAIL_TO',
    'KATO_COMPLETION_EMAIL_SENDER_EMAIL',
    'KATO_OPERATOR_EMAIL',
})

# Each entry: (key, type, label, help, extra). ``extra`` is a dict:
#   options=[...]         for select
#   warning="..."         renders an amber inline warning
#   danger="..."          renders a red inline warning + needs the
#                          operator to tick a confirm box before the
#                          value can flip on (frontend-enforced)
#   placeholder="..."     input placeholder
SETTINGS_SCHEMA: list[dict] = [
    {
        'id': 'general',
        'label': 'General',
        'title': 'General',
        'description': 'Core orchestration knobs — backend, logging, '
                       'parallelism, workspace + discovery.',
        'fields': [
            ('KATO_AGENT_BACKEND', 'select', 'Agent backend',
             'Which agent runs implementation/testing/review work.',
             {'options': ['openhands', 'claude']}),
            ('KATO_LOG_LEVEL', 'select', 'Log level',
             'Root log verbosity.', {'options': LOG_LEVELS}),
            ('KATO_WORKFLOW_LOG_LEVEL', 'select', 'Workflow log level',
             'Verbosity for the task-workflow logger.',
             {'options': LOG_LEVELS}),
            ('KATO_EXTERNAL_API_MAX_RETRIES', 'number',
             'External API max retries',
             'Retries for ticket/git provider API calls.', {}),
            ('KATO_WORKSPACES_ROOT', 'text', 'Workspaces root',
             'Per-task clone folder. Empty = ~/.kato/workspaces.', {}),
            ('KATO_MAX_PARALLEL_TASKS', 'number', 'Max parallel tasks',
             'How many tasks execute concurrently (1 = sequential).',
             {}),
            ('KATO_IGNORED_REPOSITORY_FOLDERS', 'text',
             'Ignored repo folders',
             'Comma-separated folder names excluded from auto-discovery.',
             {}),
            ('KATO_REPOSITORY_DENYLIST', 'text', 'Repository denylist',
             'Comma-separated repo ids kato must NEVER touch '
             '(secrets-vault, regulated-data, …). Boot-time refusal.',
             {}),
            ('KATO_WEBSERVER_PORT', 'number', 'Webserver port',
             'Host port for the planning UI (Flask).', {}),
            ('KATO_TASK_PUBLISH_MAX_RETRIES', 'number',
             'Publish max retries',
             'Retries for the publish step (PR + move-to-review).', {}),
            ('KATO_WORKSPACE_REVIEW_TTL_SECONDS', 'number',
             'Workspace review TTL (s)',
             'How long a review-state workspace survives before '
             'cleanup. 0 = disable TTL cleanup.', {}),
            ('KATO_OPERATOR_EMAIL', 'text', 'Operator email',
             'Recorded as approved_by on approvals. Audit only.', {}),
            ('KATO_ARCHITECTURE_DOC_PATH', 'text',
             'Architecture doc path',
             'Markdown file appended to Claude\'s system prompt on '
             'every spawn. Re-read each spawn.', {}),
            ('KATO_APPROVED_REPOSITORIES_PATH', 'text',
             'Approvals sidecar path',
             'Override the approvals JSON location. Empty = '
             '~/.kato/approved-repositories.json.', {}),
        ],
    },
    {
        'id': 'claude_agent',
        'label': 'Claude agent',
        'title': 'Claude agent',
        'description': 'Used when Agent backend = claude. Auth: set '
                       'CLAUDE_CODE_OAUTH_TOKEN (Max/Pro) OR '
                       'ANTHROPIC_API_KEY (pay-per-token).',
        'fields': [
            ('KATO_CLAUDE_BINARY', 'text', 'Claude binary',
             'Path to the `claude` CLI. Plain `claude` works on PATH.',
             {}),
            ('KATO_CLAUDE_MODEL', 'text', 'Model override',
             'e.g. claude-opus-4-7. Empty = Claude Code default.', {}),
            ('KATO_CLAUDE_MAX_TURNS', 'number', 'Max turns',
             'Cap on agent turns per task. Empty = no cap.', {}),
            ('KATO_CLAUDE_EFFORT', 'select', 'Reasoning effort',
             'Passed via --effort. Higher = more tokens/time.',
             {'options': ['', 'low', 'medium', 'high', 'xhigh', 'max']}),
            ('KATO_CLAUDE_ALLOWED_TOOLS', 'text', 'Allowed tools',
             'Comma-separated --allowedTools. Empty → safe default '
             'when bypass is off.', {}),
            ('KATO_CLAUDE_DISALLOWED_TOOLS', 'text', 'Disallowed tools',
             'Comma-separated --disallowedTools.', {}),
            ('KATO_CLAUDE_TIMEOUT_SECONDS', 'number',
             'Per-task timeout (s)',
             'Subprocess timeout for the Claude CLI per task.', {}),
            ('KATO_CLAUDE_MODEL_SMOKE_TEST_ENABLED', 'bool',
             'Model smoke test',
             'Boot-time model-access check. Off by default (spend).',
             {}),
            ('ANTHROPIC_API_KEY', 'secret', 'Anthropic API key',
             'Pay-per-token auth. Use this OR the OAuth token.', {}),
            ('CLAUDE_CODE_OAUTH_TOKEN', 'secret',
             'Claude Code OAuth token',
             'Max/Pro plan token from `claude setup-token`. '
             'Recommended for Docker.', {}),
        ],
    },
    {
        'id': 'sandbox',
        'label': 'Sandbox',
        'title': 'Sandbox & permission bypass',
        'description': 'The containment + prompt layers. These change '
                       'how much the agent can do WITHOUT asking. Read '
                       'every warning — some of these make kato refuse '
                       'to boot in certain environments.',
        'fields': [
            ('KATO_CLAUDE_DOCKER', 'bool', 'Docker sandbox',
             'Wrap every Claude spawn in the hardened Docker sandbox '
             '(workspace bind-mount only, default-DROP egress '
             'firewall, capability drop, read-only rootfs, audit '
             'log). Independent of bypass.',
             {'warning': 'Requires a working Docker daemon. Kato '
                         'REFUSES to boot if this is true and Docker '
                         'is unavailable — it will not silently fall '
                         'back to host execution.'}),
            ('KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS', 'bool',
             'Pre-approve read-only tools',
             'Skip the per-tool prompt for a hardcoded read-only '
             'Bash allowlist (grep / cat / ls / find …).',
             {'warning': 'Requires Docker sandbox = true. Without the '
                         'sandbox even `grep` runs on the host and can '
                         'read SSH keys / any file you can read — kato '
                         'refuses at startup if this is on and Docker '
                         'is off.'}),
            ('KATO_CLAUDE_BYPASS_PERMISSIONS', 'bool',
             'Bypass ALL permission prompts',
             'The agent runs every tool (Bash, Edit, Write, …) with '
             'NO prompt, inside the Docker sandbox. See '
             'BYPASS_PROTECTIONS.md / SECURITY.md.',
             {'danger': 'DANGEROUS. Kato will REFUSE to start if: it '
                        'runs as root; Docker sandbox is off; the '
                        'environment is non-interactive (CI / cron / '
                        'systemd / Docker); or you answer "no" at '
                        'either startup confirmation prompt. When on, '
                        'kato writes an unmissable banner. Only enable '
                        'if you understand BYPASS_PROTECTIONS.md.'}),
        ],
    },
    {
        'id': 'security_scanner',
        'label': 'Security scanner',
        'title': 'Pre-execution security scanner',
        'description': 'Scans each task\'s workspace clone for '
                       'committed secrets / vulnerable deps / '
                       'dangerous patterns before the agent runs. '
                       'Blocks on CRITICAL by default.',
        'fields': [
            ('KATO_SECURITY_SCANNER_ENABLED', 'bool',
             'Scanner enabled',
             'Master switch. OFF is NOT recommended for teams that '
             'ship to production.',
             {'warning': 'Disabling removes the committed-secret / '
                         'vulnerable-dep gate entirely.'}),
            ('KATO_SECURITY_RUNNER_ENV_FILE', 'bool',
             'Runner: .env / secret scan',
             'Scan for committed .env files and hardcoded secrets '
             '(API keys, tokens, passwords) in the workspace.', {}),
            ('KATO_SECURITY_RUNNER_DETECT_SECRETS', 'bool',
             'Runner: detect-secrets',
             'Run Yelp detect-secrets to find high-entropy strings '
             'and known secret patterns across all committed files.', {}),
            ('KATO_SECURITY_RUNNER_BANDIT', 'bool',
             'Runner: bandit (Python)',
             'Run bandit static analysis on Python files to catch '
             'common security issues (SQL injection, shell injection, '
             'insecure deserialization, etc.).', {}),
            ('KATO_SECURITY_RUNNER_SAFETY', 'bool',
             'Runner: safety (deps)',
             'Check Python dependencies against the Safety DB for '
             'known CVEs. Blocks on CRITICAL vulnerabilities.', {}),
            ('KATO_SECURITY_RUNNER_NPM_AUDIT', 'bool',
             'Runner: npm-audit',
             'Run npm audit on Node.js projects. Off by default — '
             'noisy transitive-dep CVEs.', {}),
            ('KATO_SECURITY_TIMEOUT_SECRETS', 'number',
             'Timeout: secrets (s)',
             'Max seconds for the secret-scan runner before it is '
             'killed. Increase for very large repos.', {}),
            ('KATO_SECURITY_TIMEOUT_DEPENDENCIES', 'number',
             'Timeout: dependencies (s)',
             'Max seconds for the dependency-vulnerability runner.', {}),
            ('KATO_SECURITY_TIMEOUT_CODE_PATTERNS', 'number',
             'Timeout: code patterns (s)',
             'Max seconds for the bandit / code-pattern runner.', {}),
        ],
    },
    {
        'id': 'email_slack',
        'label': 'Email & Slack',
        'title': 'Email & Slack notifications',
        'description': 'Server-side notifications kato sends on task '
                       'failure / completion. (Browser notifications '
                       'are the separate Notifications tab.)',
        'fields': [
            ('KATO_FAILURE_EMAIL_ENABLED', 'bool',
             'Failure email enabled',
             'Send an email when a task fails. Requires Brevo API key '
             'and sender/recipient fields below.', {}),
            ('KATO_FAILURE_EMAIL_TEMPLATE_ID', 'number',
             'Failure template id',
             'Brevo transactional template ID used for failure emails.', {}),
            ('KATO_FAILURE_EMAIL_TO', 'text', 'Failure email to',
             'Recipient email address for failure notifications.', {}),
            ('KATO_FAILURE_EMAIL_SENDER_NAME', 'text',
             'Failure sender name',
             'Display name shown in the From field of failure emails.', {}),
            ('KATO_FAILURE_EMAIL_SENDER_EMAIL', 'text',
             'Failure sender email',
             'From address for failure notification emails.', {}),
            ('KATO_COMPLETION_EMAIL_ENABLED', 'bool',
             'Completion email enabled',
             'Send an email when a task completes successfully.', {}),
            ('KATO_COMPLETION_EMAIL_TEMPLATE_ID', 'number',
             'Completion template id',
             'Brevo transactional template ID for completion emails.', {}),
            ('KATO_COMPLETION_EMAIL_TO', 'text',
             'Completion email to',
             'Recipient email address for completion notifications.', {}),
            ('KATO_COMPLETION_EMAIL_SENDER_NAME', 'text',
             'Completion sender name',
             'Display name in the From field of completion emails.', {}),
            ('KATO_COMPLETION_EMAIL_SENDER_EMAIL', 'text',
             'Completion sender email',
             'From address for completion notification emails.', {}),
            ('EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY', 'secret',
             'Brevo/SendinBlue API key',
             'API key for Brevo (formerly SendinBlue) transactional '
             'email. Required for any email notification to work.', {}),
            ('SLACK_WEBHOOK_URL_ERRORS_EMAIL', 'secret',
             'Slack error webhook URL',
             'Incoming webhook URL for a Slack channel. Kato posts '
             'task failure summaries here.', {}),
        ],
    },
    {
        'id': 'openhands',
        'label': 'OpenHands',
        'title': 'OpenHands backend',
        'description': 'Used when Agent backend = openhands. Container, '
                       'LLM, scan-loop, and runtime config.',
        'fields': [
            ('OPENHANDS_BASE_URL', 'text', 'Base URL',
             'URL of the running OpenHands server, e.g. '
             'http://localhost:3000.', {}),
            ('OPENHANDS_API_KEY', 'secret', 'API key',
             'API key for authenticating with the OpenHands server.', {}),
            ('OPENHANDS_SKIP_TESTING', 'bool', 'Skip testing',
             'Skip the post-implementation test run. Useful when the '
             'repo has no automated tests or they are flaky.', {}),
            ('OPENHANDS_TESTING_CONTAINER_ENABLED', 'bool',
             'Testing container enabled',
             'Spin up a separate OpenHands container dedicated to '
             'running tests (isolated from the implementation run).', {}),
            ('OPENHANDS_TESTING_BASE_URL', 'text', 'Testing base URL',
             'URL of the testing-only OpenHands container, if separate '
             'from the implementation container.', {}),
            ('OPENHANDS_TESTING_PORT', 'number', 'Testing port',
             'Host port for the testing OpenHands container.', {}),
            ('OPENHANDS_CONTAINER_LOG_ALL_EVENTS', 'bool',
             'Log all container events',
             'Stream every OpenHands event (tool calls, observations) '
             'to kato logs. Verbose — use for debugging only.', {}),
            ('OPENHANDS_PORT', 'number', 'Container port',
             'Host port exposed by the OpenHands Docker container.', {}),
            ('OPENHANDS_PULL_POLICY', 'select', 'Pull policy',
             'When to pull the OpenHands Docker image: missing = only '
             'if not cached, always = on every start, never = never.',
             {'options': ['missing', 'always', 'never']}),
            ('OPENHANDS_LOG_LEVEL', 'select', 'Log level',
             'Log verbosity inside the OpenHands container.',
             {'options': LOG_LEVELS}),
            ('OH_SECRET_KEY', 'secret', 'OH secret key',
             'Stable random secret for OpenHands secret persistence.',
             {}),
            ('OPENHANDS_STATE_DIR', 'text', 'State dir',
             'Directory on the host where OpenHands persists agent '
             'state between runs.', {}),
            ('OPENHANDS_WEB_URL', 'text', 'Web URL',
             'Public URL of the OpenHands UI, used when generating '
             'links in PR comments.', {}),
            ('OPENHANDS_RUNTIME', 'text', 'Runtime',
             'OpenHands runtime backend, e.g. docker or e2b.', {}),
            ('OPENHANDS_SSH_AUTH_SOCK_HOST_PATH', 'text',
             'SSH auth sock host path',
             'Host path of the SSH agent socket, bind-mounted into '
             'the container so the agent can push to git.', {}),
            ('OPENHANDS_LLM_MODEL', 'text', 'LLM model',
             'Model identifier passed to OpenHands, e.g. '
             'anthropic/claude-sonnet-4-5 or openrouter/openai/gpt-4o.', {}),
            ('OPENHANDS_LLM_API_KEY', 'secret', 'LLM API key',
             'API key for the LLM provider (Anthropic, OpenRouter, '
             'Azure, etc.).', {}),
            ('OPENHANDS_LLM_BASE_URL', 'text', 'LLM base URL',
             'Override the default LLM API endpoint, e.g. for '
             'OpenRouter (https://openrouter.ai/api/v1) or Azure.', {}),
            ('OPENHANDS_MODEL_SMOKE_TEST_ENABLED', 'bool',
             'LLM smoke test',
             'Run a cheap test call at boot to verify LLM connectivity '
             'before the first task. Costs one API call per restart.', {}),
            ('OPENHANDS_TESTING_LLM_MODEL', 'text', 'Testing LLM model',
             'Model used by the testing container. Defaults to the '
             'primary LLM model if unset.', {}),
            ('OPENHANDS_TESTING_LLM_API_KEY', 'secret',
             'Testing LLM API key',
             'API key for the testing container LLM. Defaults to the '
             'primary LLM API key if unset.', {}),
            ('OPENHANDS_TESTING_LLM_BASE_URL', 'text',
             'Testing LLM base URL',
             'Base URL for the testing container LLM. Defaults to the '
             'primary LLM base URL if unset.', {}),
            ('OPENHANDS_LLM_API_VERSION', 'text', 'LLM API version',
             'API version string required by some providers '
             '(e.g. Azure OpenAI: 2024-02-01).', {}),
            ('OPENHANDS_LLM_NUM_RETRIES', 'number', 'LLM num retries',
             'How many times OpenHands retries a failed LLM call '
             'before giving up.', {}),
            ('OPENHANDS_LLM_TIMEOUT', 'number', 'LLM timeout',
             'Per-request timeout in seconds for LLM API calls.', {}),
            ('OPENHANDS_POLL_INTERVAL_SECONDS', 'number',
             'Poll interval (s)',
             'How often kato polls the OpenHands server for task '
             'status updates.', {}),
            ('OPENHANDS_MAX_POLL_ATTEMPTS', 'number', 'Max poll attempts',
             'Maximum number of status polls before kato declares '
             'the task timed out.', {}),
            ('OPENHANDS_TASK_SCAN_STARTUP_DELAY_SECONDS', 'number',
             'Scan startup delay (s)',
             'Seconds kato waits after boot before the first task '
             'scan, giving services time to initialise.', {}),
            ('OPENHANDS_TASK_SCAN_INTERVAL_SECONDS', 'number',
             'Scan interval (s)',
             'How often the OpenHands task-scan loop checks for new '
             'assigned tasks.', {}),
            ('OPENHANDS_LLM_DISABLE_VISION', 'text', 'Disable vision',
             'Set to true to disable image/screenshot inputs to the '
             'LLM (for models that do not support vision).', {}),
            ('OPENHANDS_LLM_DROP_PARAMS', 'text', 'Drop params',
             'Comma-separated LLM parameters to strip from every '
             'request (e.g. top_p for providers that reject it).', {}),
            ('OPENHANDS_LLM_CACHING_PROMPT', 'text', 'Caching prompt',
             'Set to true to enable Anthropic prompt-caching on the '
             'system prompt, reducing cost on repeated tasks.', {}),
        ],
    },
    {
        'id': 'infra',
        'label': 'Docker / infra',
        'title': 'Docker & infrastructure',
        'description': 'Compose / image config for containerised runs.',
        'fields': [
            ('MOUNT_DOCKER_DATA_ROOT', 'text', 'Docker data root',
             'Override Docker\'s data root directory on the host. '
             'Useful when the default /var/lib/docker is on a small '
             'partition.', {}),
            ('KATO_AGENT_SERVER_IMAGE_REPOSITORY', 'text',
             'Agent server image repo',
             'Docker image repository for the kato agent server '
             'container, e.g. ghcr.io/myorg/kato-agent.', {}),
            ('KATO_AGENT_SERVER_IMAGE_TAG', 'text',
             'Agent server image tag',
             'Docker image tag to pull for the agent server, '
             'e.g. latest or a specific SHA.', {}),
        ],
    },
    {
        'id': 'aws',
        'label': 'AWS / Bedrock',
        'title': 'AWS / Bedrock',
        'description': 'Optional — only for Bedrock-backed LLM setups.',
        'fields': [
            ('AWS_ACCESS_KEY_ID', 'text', 'Access key id',
             'AWS IAM access key ID for authenticating with Bedrock.', {}),
            ('AWS_SECRET_ACCESS_KEY', 'secret', 'Secret access key',
             'AWS IAM secret access key paired with the access key ID.', {}),
            ('AWS_REGION_NAME', 'text', 'Region',
             'AWS region where your Bedrock models are available, '
             'e.g. us-east-1.', {}),
            ('AWS_SESSION_TOKEN', 'secret', 'Session token',
             'Temporary session token for short-lived IAM credentials '
             '(STS AssumeRole). Leave blank for long-lived keys.', {}),
            ('AWS_BEARER_TOKEN_BEDROCK', 'secret', 'Bedrock bearer token',
             'Bearer token for direct Bedrock API authentication '
             '(alternative to IAM key/secret).', {}),
        ],
    },
    {
        'id': 'openrouter',
        'label': 'OpenRouter',
        'title': 'OpenRouter LLM gateway',
        'description': 'Used when the LLM model starts with openrouter/. '
                       'Routes requests through openrouter.ai so you can '
                       'pick any hosted model (GPT-4o, Gemini, Mistral, '
                       'etc.) with a single API key.',
        'fields': [
            ('OPENHANDS_LLM_BASE_URL', 'text', 'Base URL',
             'OpenRouter API endpoint. '
             'Default: https://openrouter.ai/api/v1 — set automatically '
             'when the LLM model starts with openrouter/.',
             {'placeholder': 'https://openrouter.ai/api/v1'}),
            ('OPENHANDS_LLM_API_KEY', 'secret', 'API key',
             'Your OpenRouter API key (sk-or-…). '
             'Required to authenticate requests to openrouter.ai.',
             {}),
            ('OPENHANDS_LLM_MODEL', 'text', 'Model',
             'Model identifier with openrouter/ prefix, e.g. '
             'openrouter/openai/gpt-4o or '
             'openrouter/anthropic/claude-3.5-haiku.',
             {'placeholder': 'openrouter/openai/gpt-4o'}),
            ('OPENHANDS_LLM_NUM_RETRIES', 'number', 'Retries',
             'Number of times to retry a failed LLM call before giving up.',
             {}),
            ('OPENHANDS_LLM_TIMEOUT', 'number', 'Timeout (s)',
             'Per-request timeout in seconds for OpenRouter API calls.',
             {}),
        ],
    },
]


def _schema_type_lookup() -> dict[str, dict]:
    """Return {key: {type, options?, ...}} for every schema-declared field."""
    lookup: dict[str, dict] = {}
    for section in SETTINGS_SCHEMA:
        for key, ftype, _label, _help, extra in section['fields']:
            if key not in lookup:
                lookup[key] = {'type': ftype, **(extra or {})}
    return lookup


def _check_type(key: str, val: str, meta: dict) -> str | None:
    ftype = meta.get('type', 'text')
    if ftype == 'select':
        options: list[str] = meta.get('options', [])
        if options and val not in options:
            return f'{key}: must be one of {options}; got "{val}"'
    elif ftype == 'number':
        try:
            parsed = float(val)
            if not math.isfinite(parsed) or parsed < 0:
                raise ValueError
        except (ValueError, TypeError):
            return f'{key}: must be a non-negative number; got "{val}"'
    elif ftype == 'bool' and val not in ('true', 'false'):
        return f'{key}: must be "true" or "false"; got "{val}"'
    return None


def _check_url(key: str, val: str) -> str | None:
    if key in _URL_KEYS and not val.startswith(('http://', 'https://')):
        return f'{key}: must be an http:// or https:// URL; got "{val}"'
    return None


def _check_email(key: str, val: str) -> str | None:
    if key not in _EMAIL_KEYS:
        return None
    parts = val.split('@', 1)
    if len(parts) != 2 or not parts[0] or '.' not in parts[1]:
        return f'{key}: must be a valid email address; got "{val}"'
    return None


def validate_settings_values(updates: dict[str, str]) -> list[str]:
    """Validate a settings dict before it is persisted.

    Covers:
    - ``select`` fields: value must be one of the declared options
    - ``number`` fields: value must parse as a finite non-negative number
    - ``bool`` fields: value must be ``"true"`` or ``"false"``
    - URL keys: value must start with ``http://`` or ``https://``
    - Email keys: value must contain ``@`` with a dotted domain

    Empty-string values are skipped — clearing a field is always valid.
    Returns a (possibly empty) list of human-readable error strings.
    """
    lookup = _schema_type_lookup()
    errors: list[str] = []
    for key, raw in updates.items():
        val = str(raw).strip()
        if not val:
            continue
        for check in (
            _check_type(key, val, lookup.get(key, {})),
            _check_url(key, val),
            _check_email(key, val),
        ):
            if check:
                errors.append(check)
    return errors


def all_settings_keys() -> set[str]:
    """Every key the generic settings route may write — the whitelist."""
    keys: set[str] = set()
    for section in SETTINGS_SCHEMA:
        for field in section['fields']:
            keys.add(field[0])
    return keys


def schema_for_api() -> list[dict]:
    """JSON-serialisable schema (tuples → dicts) for the GET response."""
    out = []
    for section in SETTINGS_SCHEMA:
        fields = []
        for key, ftype, label, help_text, extra in section['fields']:
            entry = {
                'key': key,
                'type': ftype,
                'label': label,
                'help': help_text,
            }
            entry.update(extra or {})
            fields.append(entry)
        out.append({
            'id': section['id'],
            'label': section['label'],
            'title': section['title'],
            'description': section['description'],
            'fields': fields,
        })
    return out
