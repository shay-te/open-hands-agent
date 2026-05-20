# Environment Reference — kato

The list below mirrors `.env.example`. For provider-specific setup snippets see [readmeIssuePlatforms.md](readmeIssuePlatforms.md) and [readmeOpenHands.md](readmeOpenHands.md).

## Ticket And Repository

| Variable | What it does |
| --- | --- |
| `KATO_ISSUE_PLATFORM` | Selects the active issue platform. Supported values are `youtrack`, `jira`, `github`, `gitlab`, and `bitbucket`. |
| `KATO_AGENT_BACKEND` | Selects the active agent backend. Supported values are `openhands` (default) and `claude`. |
| `YOUTRACK_API_BASE_URL` | YouTrack API base URL. |
| `YOUTRACK_API_TOKEN` | YouTrack API token. |
| `YOUTRACK_PROJECT` | YouTrack project key used to fetch tasks. |
| `YOUTRACK_ASSIGNEE` | YouTrack assignee to scan for tasks. |
| `YOUTRACK_PROGRESS_STATE_FIELD` | YouTrack field used for the in-progress transition. |
| `YOUTRACK_PROGRESS_STATE` | YouTrack value used for the in-progress transition. |
| `YOUTRACK_REVIEW_STATE_FIELD` | YouTrack field used for the review transition. |
| `YOUTRACK_REVIEW_STATE` | YouTrack value used for the review transition. |
| `YOUTRACK_ISSUE_STATES` | YouTrack issue states that qualify for processing. |
| `JIRA_API_BASE_URL` | Jira API base URL. |
| `JIRA_API_TOKEN` | Jira API token. |
| `JIRA_EMAIL` | Jira user email for authentication. |
| `JIRA_PROJECT` | Jira project key used to fetch tasks. |
| `JIRA_ASSIGNEE` | Jira assignee to scan for tasks. |
| `JIRA_PROGRESS_STATE_FIELD` | Jira field used for the in-progress transition. |
| `JIRA_PROGRESS_STATE` | Jira value used for the in-progress transition. |
| `JIRA_REVIEW_STATE_FIELD` | Jira field used for the review transition. |
| `JIRA_REVIEW_STATE` | Jira value used for the review transition. |
| `JIRA_ISSUE_STATES` | Jira issue states that qualify for processing. |
| `GITHUB_API_BASE_URL` | GitHub Issues API base URL. |
| `GITHUB_API_TOKEN` | GitHub API token. Also used for GitHub git push and PR creation when needed. |
| `GITHUB_OWNER` | GitHub repository owner used to scope issues. |
| `GITHUB_REPO` | GitHub repository name used to scope issues. |
| `GITHUB_ASSIGNEE` | GitHub assignee to scan for tasks. |
| `GITHUB_PROGRESS_STATE_FIELD` | GitHub Issues field used for the in-progress transition. |
| `GITHUB_PROGRESS_STATE` | GitHub Issues value used for the in-progress transition. |
| `GITHUB_REVIEW_STATE_FIELD` | GitHub Issues field used for the review transition. |
| `GITHUB_REVIEW_STATE` | GitHub Issues value used for the review transition. |
| `GITHUB_ISSUE_STATES` | GitHub Issues states that qualify for processing. |
| `GITLAB_API_BASE_URL` | GitLab Issues API base URL. |
| `GITLAB_API_TOKEN` | GitLab API token. Also used for GitLab git push and merge request creation when needed. |
| `GITLAB_PROJECT` | GitLab project path used to scope issues. |
| `GITLAB_ASSIGNEE` | GitLab assignee to scan for tasks. |
| `GITLAB_PROGRESS_STATE_FIELD` | GitLab Issues field used for the in-progress transition. |
| `GITLAB_PROGRESS_STATE` | GitLab Issues value used for the in-progress transition. |
| `GITLAB_REVIEW_STATE_FIELD` | GitLab Issues field used for the review transition. |
| `GITLAB_REVIEW_STATE` | GitLab Issues value used for the review transition. |
| `GITLAB_ISSUE_STATES` | GitLab Issues states that qualify for processing. |
| `BITBUCKET_API_BASE_URL` | Bitbucket Issues API base URL. |
| `BITBUCKET_API_TOKEN` | Bitbucket API token. Used as the password for Bitbucket git auth and Bitbucket REST API auth. |
| `BITBUCKET_USERNAME` | Bitbucket username used for git push auth. |
| `BITBUCKET_API_EMAIL` | Atlassian account email used for Bitbucket REST API auth with API tokens. |
| `BITBUCKET_WORKSPACE` | Bitbucket workspace used to scope issues. |
| `BITBUCKET_REPO_SLUG` | Bitbucket repository slug used to scope issues. |
| `BITBUCKET_ASSIGNEE` | Bitbucket assignee to scan for tasks. |
| `BITBUCKET_PROGRESS_STATE_FIELD` | Bitbucket Issues field used for the in-progress transition. |
| `BITBUCKET_PROGRESS_STATE` | Bitbucket Issues value used for the in-progress transition. |
| `BITBUCKET_REVIEW_STATE_FIELD` | Bitbucket Issues field used for the review transition. |
| `BITBUCKET_REVIEW_STATE` | Bitbucket Issues value used for the review transition. |
| `BITBUCKET_ISSUE_STATES` | Bitbucket Issues states that qualify for processing. |
| `REPOSITORY_ROOT_PATH` | Root folder where the agent scans for checked-out repositories. |
| `MOUNT_DOCKER_DATA_ROOT` | Host folder that holds all Docker bind-mounted data under one parent directory. |
| `KATO_IGNORED_REPOSITORY_FOLDERS` | Comma-separated folder names to exclude from repository auto-discovery. |

## Kato Runtime

| Variable | What it does |
| --- | --- |
| `OPENHANDS_BASE_URL` | Base URL for the primary OpenHands server. |
| `OPENHANDS_API_KEY` | API key for the primary OpenHands server. |
| `OPENHANDS_SKIP_TESTING` | Skips the testing validation conversation and publishes after implementation. |
| `OPENHANDS_TESTING_CONTAINER_ENABLED` | Enables the optional dedicated testing OpenHands container. |
| `OPENHANDS_TESTING_BASE_URL` | Base URL for the dedicated testing OpenHands server. |
| `OPENHANDS_TESTING_PORT` | Host port used for the optional testing container. |
| `OPENHANDS_CONTAINER_LOG_ALL_EVENTS` | Enables all OpenHands event logging inside the `openhands` container. |
| `KATO_LOG_LEVEL` | Log level for the agent app process. |
| `KATO_WORKFLOW_LOG_LEVEL` | Log level for workflow-specific logs. |
| `OPENHANDS_POLL_INTERVAL_SECONDS` | Delay between Kato conversation polling attempts. |
| `OPENHANDS_MAX_POLL_ATTEMPTS` | Maximum number of times the agent waits for an Kato conversation result. |
| `OPENHANDS_TASK_SCAN_STARTUP_DELAY_SECONDS` | Delay before the agent starts scanning for tasks after startup. |
| `OPENHANDS_TASK_SCAN_INTERVAL_SECONDS` | Delay between task scan cycles. |
| `KATO_FAILURE_EMAIL_ENABLED` | Enables failure notification emails. |
| `KATO_FAILURE_EMAIL_TEMPLATE_ID` | Template id used for failure notification emails. |
| `KATO_FAILURE_EMAIL_TO` | Recipient address for failure notification emails. |
| `KATO_FAILURE_EMAIL_SENDER_NAME` | Sender name for failure notification emails. |
| `KATO_FAILURE_EMAIL_SENDER_EMAIL` | Sender email for failure notification emails. |
| `KATO_COMPLETION_EMAIL_ENABLED` | Enables completion notification emails. |
| `KATO_COMPLETION_EMAIL_TEMPLATE_ID` | Template id used for completion notification emails. |
| `KATO_COMPLETION_EMAIL_TO` | Recipient address for completion notification emails. |
| `KATO_COMPLETION_EMAIL_SENDER_NAME` | Sender name for completion notification emails. |
| `KATO_COMPLETION_EMAIL_SENDER_EMAIL` | Sender email for completion notification emails. |
| `EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY` | SendInBlue API key used by `email-core-lib`. |
| `SLACK_WEBHOOK_URL_ERRORS_EMAIL` | Slack webhook used by `email-core-lib` error reporting. |

The `openhands` container reuses the same `OPENHANDS_LLM_*` and `AWS_*` values from the shared `.env` file, so the Bedrock configuration is defined once. `OPENHANDS_CONTAINER_LOG_ALL_EVENTS` is the only service-specific override for that container.

## OpenHands Container

| Variable | What it does |
| --- | --- |
| `OPENHANDS_PORT` | Host port exposed for the OpenHands container. |
| `OPENHANDS_LOG_LEVEL` | OpenHands container log level. |
| `OH_SECRET_KEY` | OpenHands secret key used to persist secrets safely across restarts. |
| `OPENHANDS_STATE_DIR` | Host path for OpenHands state storage. |
| `OPENHANDS_WEB_URL` | Public URL that OpenHands should advertise. |
| `OPENHANDS_RUNTIME` | Runtime backend used by OpenHands. |
| `KATO_AGENT_SERVER_IMAGE_REPOSITORY` | Agent server image repository used by the OpenHands container. |
| `KATO_AGENT_SERVER_IMAGE_TAG` | Agent server image tag used by the OpenHands container. |
| `OPENHANDS_SSH_AUTH_SOCK_HOST_PATH` | Host SSH agent socket path forwarded into Docker for SSH git remotes. |

## OpenHands LLM

| Variable | What it does |
| --- | --- |
| `OPENHANDS_LLM_MODEL` | Primary OpenHands model name. |
| `OPENHANDS_LLM_API_KEY` | API key for the primary OpenHands model. |
| `OPENHANDS_LLM_BASE_URL` | Optional custom base URL for the primary OpenHands model. OpenRouter typically uses `https://openrouter.ai/api/v1`. |
| `OPENHANDS_MODEL_SMOKE_TEST_ENABLED` | Runs an extra startup model smoke test during connection validation. |
| `OPENHANDS_TESTING_LLM_MODEL` | Model name used by the dedicated testing OpenHands server. |
| `OPENHANDS_TESTING_LLM_API_KEY` | API key used by the dedicated testing OpenHands server. |
| `OPENHANDS_TESTING_LLM_BASE_URL` | Base URL used by the dedicated testing OpenHands server. OpenRouter testing models should use `https://openrouter.ai/api/v1`. |
| `OPENHANDS_LLM_API_VERSION` | Optional API version passed through to the OpenHands LLM config. |
| `OPENHANDS_LLM_NUM_RETRIES` | Optional LLM retry count passed through to OpenHands. |
| `OPENHANDS_LLM_TIMEOUT` | Optional LLM timeout passed through to OpenHands. |
| `OPENHANDS_LLM_DISABLE_VISION` | Optional OpenHands flag to disable vision features. |
| `OPENHANDS_LLM_DROP_PARAMS` | Optional OpenHands flag for dropping unsupported model params. |
| `OPENHANDS_LLM_CACHING_PROMPT` | Optional caching prompt passed through to OpenHands. |
| `AWS_ACCESS_KEY_ID` | AWS access key for Bedrock-backed models or AWS auth in Docker. |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key for Bedrock-backed models or AWS auth in Docker. |
| `AWS_REGION_NAME` | AWS region used for Bedrock-backed models or AWS auth in Docker. |
| `AWS_SESSION_TOKEN` | Optional AWS session token for temporary Bedrock credentials. |
| `AWS_BEARER_TOKEN_BEDROCK` | Bedrock bearer token alternative to AWS access keys. |

## Claude CLI Backend

| Variable | What it does |
| --- | --- |
| `KATO_CLAUDE_BINARY` | Path to (or PATH name of) the Claude Code CLI binary. Defaults to `claude`. |
| `KATO_CLAUDE_MODEL` | Optional model id passed via `--model` (e.g. `claude-opus-4-7`). Empty uses the CLI default. |
| `KATO_CLAUDE_MAX_TURNS` | Optional cap on agent turns per task, passed via `--max-turns`. Empty means no cap. |
| `KATO_CLAUDE_EFFORT` | Optional reasoning depth passed via `--effort` (`low`/`medium`/`high`/`xhigh`/`max`). Empty leaves Claude on its built-in default. |
| `KATO_CLAUDE_ALLOWED_TOOLS` | Comma-separated allowlist passed via `--allowedTools`. |
| `KATO_CLAUDE_DISALLOWED_TOOLS` | Comma-separated denylist passed via `--disallowedTools`. |
| `KATO_CLAUDE_BYPASS_PERMISSIONS` | When `true`, kato runs Claude with `--permission-mode bypassPermissions` (no per-tool prompts) inside the hardened Docker sandbox. When `false` (the default), kato runs in `acceptEdits` mode and routes any permission asks back over the planning UI. Refused under root, refused under CI/Docker/cron (no TTY for confirmation), and double-prompted on the terminal at every interactive startup. See [BYPASS_PROTECTIONS.md](BYPASS_PROTECTIONS.md). |
| `KATO_CLAUDE_TIMEOUT_SECONDS` | Per-task subprocess timeout. Defaults to 1800. Minimum 60. |
| `KATO_CLAUDE_MODEL_SMOKE_TEST_ENABLED` | Runs a small `claude -p` prompt during startup validation. Off by default. |
| `KATO_ARCHITECTURE_DOC_PATH` | Optional path to a project-architecture markdown file. When set, kato appends the file's contents to Claude's system prompt on every spawn (autonomous, planning, chat-respawn) via `--append-system-prompt`. Re-read on each spawn so edits land without a kato restart. |
| `KATO_TASK_PUBLISH_MAX_RETRIES` | Retries for the publish step (per-repo PR creation + the move-to-review transition). Implementation work is not re-run. Defaults to `2` (up to 3 attempts) with exponential backoff. |
| `KATO_WORKSPACE_REVIEW_TTL_SECONDS` | How long a workspace in `review` state persists before the cleanup loop deletes it, regardless of whether the ticket is still in the review bucket. Default `3600` (1 hour). Set to `0` to disable TTL-based cleanup (legacy behavior: workspace persists until the ticket leaves both assigned and review). Review-comment processing for cleaned tickets re-clones on demand. |

The active issue provider comes from `kato.issue_platform`, which defaults to `youtrack`.
Issue states can be configured directly in `.env` with `YOUTRACK_ISSUE_STATES`, `JIRA_ISSUE_STATES`, `GITHUB_ISSUE_STATES`, `GITLAB_ISSUE_STATES`, and `BITBUCKET_ISSUE_STATES`.
The review-state target also comes from the active provider config:
- YouTrack uses `kato.youtrack.review_state_field` and `kato.youtrack.review_state`.
- Jira uses `kato.jira.review_state_field` and `kato.jira.review_state`.
- GitHub Issues uses `kato.github_issues.review_state_field` and `kato.github_issues.review_state`.
- GitLab Issues uses `kato.gitlab_issues.review_state_field` and `kato.gitlab_issues.review_state`.
- Bitbucket Issues uses `kato.bitbucket_issues.review_state_field` and `kato.bitbucket_issues.review_state`.
Processed task state, processed review-comment ids, and pull-request comment context are kept in memory during a run so the agent can skip already-completed work and poll for new review comments without writing local state.
If email notifications are enabled, install the optional dependency set with `python -m pip install -e ".[notifications]"`.
The email body text comes from [`completion_email.j2`](kato_core_lib/templates/email/completion_email.j2) and [`failure_email.j2`](kato_core_lib/templates/email/failure_email.j2), rendered with Jinja2 template variables at runtime.
The Hydra config is registered through [`hydra_plugins/kato/kato_searchpath.py`](hydra_plugins/kato/kato_searchpath.py), so standard Hydra overrides work. Example:

```bash
python -m kato_core_lib.main kato.retry.max_retries=7
```
