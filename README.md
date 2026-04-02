# OpenHands Ticket Agent

Welcome to the OpenHands Ticket Agent! This repository is structured as a [`core-lib`](https://shay-te.github.io/core-lib/) application and follows the documented `core-lib` package layout.

## Why Core-Lib

`core-lib` is a strong fit for this project because this agent is not just a script that calls one API. It has to coordinate issue platforms, repository providers, OpenHands, jobs, configuration, persistence, notifications, and testing in one place without collapsing into one large pile of glue code.

Why it works especially well here:

- `core-lib` gives the project a clean layered shape: clients for external APIs, data-access wrappers for boundaries, services for orchestration, and jobs for entrypoints. That maps directly to what this agent does every day.
- `core-lib` is built around a central application library object, which is exactly what this project needs. `OpenHandsAgentCoreLib` can be initialized once and reused from the CLI, scheduled jobs, and tests instead of rebuilding the application's wiring in multiple places.
- The `core-lib` docs emphasize fast setup, consistent structure, and reusable runtime wiring. That matters here because this project has to compose several providers cleanly: issue systems, repository systems, OpenHands, and notifications.
- `core-lib` keeps configuration-driven behavior first-class. That is one of the main reasons this repo can support multiple source issue platforms without pushing provider-specific branching into the orchestration layer.
- `core-lib` is very test-friendly. This project depends on many external systems, so confidence comes from isolating boundaries and mocking them cleanly. The layered `core-lib` structure makes that practical.
- `core-lib` reduces framework churn. Instead of spending time on custom bootstrapping, connection management, configuration loading, and lifecycle glue, this repository can stay focused on the agent's actual behavior.
- `core-lib` is an especially good choice here because it was designed by the same author for this exact style of application: modular, integration-heavy Python services that need to stay readable as they grow.

For this codebase, that means `core-lib` is not just a dependency. It is part of the design strategy. It gives the project a stable foundation, lets new providers fit an existing pattern, and keeps the repository centered on agent behavior rather than plumbing.

Reference:
- https://shay-te.github.io/core-lib/
- https://shay-te.github.io/core-lib/advantages.html

The agent is designed to:

1. Read tasks assigned to it from the configured issue platform.
   Supported issue platforms are YouTrack, Jira, GitHub Issues, GitLab Issues, and Bitbucket Issues.
   `openhands_agent.issue_platform` falls back to `openhands_agent.ticket_system`, and then defaults to `youtrack`.
   Only tasks assigned to the configured assignee and currently in one of the configured `issue_states` are eligible.
   When loading a task, the agent also reads issue comments, text attachments, and screenshot attachment metadata so OpenHands gets more complete context.
2. Read each task definition.
3. Ask OpenHands to implement the required changes.
4. Create one pull request per affected repository.
5. Add the aggregated pull request summary back to the configured issue platform, move the issue to the configured review state, and send a review-ready email.
6. Listen to pull request comments and trigger follow-up fixes.

## Structure

```text
openhands_agent/
  client/
    bitbucket_issues_client.py
    bitbucket_client.py
    github_issues_client.py
    gitlab_issues_client.py
    jira_client.py
    openhands_client.py
    ticket_client_base.py
    ticket_client_factory.py
    youtrack_client.py
  config/
    openhands_agent_core_lib.yaml
  templates/
    email/
      completion_email.txt
      failure_email.txt
  data_layers/
    data/
      review_comment.py
      task.py
    data_access/
      pull_request_data_access.py
      task_data_access.py
    service/
      agent_service.py
      implementation_service.py
  jobs/
    process_assigned_tasks.py
  main.py
  openhands_agent_core_lib.py
  openhands_agent_instance.py
scripts/
  generate_env.py
tests/
  config/
    config.yaml
```

## How It Works

This project follows the `core-lib` layering on purpose:

- `OpenHandsAgentCoreLib` wires the app once at startup, builds the clients, data-access objects, and services, and validates the external connections before work starts.
- `client/` contains provider-specific API code for issue platforms, repository providers, and OpenHands.
- `data_layers/data_access/` stays focused on boundary work such as ticket updates and pull-request API calls.
- `data_layers/service/` owns the business workflow. This is where task selection, state transitions, repository preparation, OpenHands runs, publishing, notifications, and review-comment handling live.

That separation matters because the main service flow should read like the actual workflow:

1. Load the assigned tasks from the configured issue platform.
2. Skip tasks that were already processed.
3. Resolve the repositories mentioned by the task and make sure each local checkout is safe to use.
4. Move the task to the in-progress state and add a started comment.
5. Ask OpenHands to implement the task.
6. Re-prepare the task branches and run the testing validation step.
7. Publish branch updates and open one pull request per affected repository.
8. Add the pull-request summary back to the task, move the task to the review state, and send the completion notification.

Tasks are processed sequentially, one after the other, so repository state from one task does not leak into the next one.

### Task Workflow

For each eligible task, the service does these checks and steps:

1. Read the full task context, including issue comments and any supported text attachments.
2. Infer the affected repositories from the task summary and description.
3. Validate that every repository is available locally, on the expected destination branch, and in a clean state before starting new work.
4. Build the task branch name for each repository and prepare those branches locally.
5. Run the implementation prompt through the main OpenHands client.
6. Run the testing prompt through the testing OpenHands client.
7. Commit and push the branch updates, then create pull requests or merge requests through the repository provider API.
8. Remember the pull-request context so later review comments can be mapped back to the correct repository, branch, task, and OpenHands session.

If any repository cannot be published, the successful pull requests are kept, the task is not moved to the review state, and the failure is reported clearly instead of being hidden.

### Review Comment Workflow

After task processing, the agent checks tracked review pull requests for unseen comments:

1. Look only at pull requests that belong to tasks already moved into the review state.
2. Load the saved pull-request context for the repository, branch, task, and OpenHands session.
3. Prepare the same working branch again.
4. Ask OpenHands to address the review comment in the context of the full comment thread.
5. Publish the branch update back to the same pull request branch.
6. Resolve the review comment when the provider supports it.
7. Persist the processed comment id so the same review comment is not handled twice after later polls or restarts.

### Testing OpenHands Routing

Implementation always uses the main OpenHands server from `OPENHANDS_BASE_URL`.

Testing uses:

- the dedicated testing server from `OPENHANDS_TESTING_BASE_URL` when `OPENHANDS_TESTING_CONTAINER_ENABLED=true`
- the main `OPENHANDS_BASE_URL` when `OPENHANDS_TESTING_CONTAINER_ENABLED=false`
- no testing conversation at all when `OPENHANDS_SKIP_TESTING=true`

When the testing container is enabled and `OPENHANDS_SKIP_TESTING=false`, `make compose-up` starts Docker Compose with the `testing` profile so the extra `openhands-testing` service is available. When it is disabled, no dedicated testing server is started and the agent keeps testing on the main OpenHands instance. When `OPENHANDS_SKIP_TESTING=true`, the agent skips the validation step entirely and `make compose-up` stays on the normal profile even if the dedicated testing container is enabled.

## Required Environment

For the shortest local setup path, use the interactive configurator:

```bash
make bootstrap
make configure
make doctor
make run
```

`make configure` runs `python scripts/generate_env.py --output .env` and writes a first-pass `.env` for you. It asks:

- where your tasks live
- where your source code lives
- which issue states should be processed
- which review state and field should be used
- the first repository, OpenHands, and optional email settings

The configurator uses the same style of shell prompts used by `core-lib`, so the setup flow stays consistent with the rest of the stack.

If you prefer to edit the file manually, start here:

```bash
cp .env.example .env
```

Use `OPENHANDS_AGENT_ISSUE_PLATFORM` for new setups. `OPENHANDS_AGENT_TICKET_SYSTEM` is still accepted as a backward-compatible alias.

## Environment Reference

The list below mirrors `.env.example`.

### Ticket And Repository

| Variable | What it does |
| --- | --- |
| `OPENHANDS_AGENT_ISSUE_PLATFORM` | Selects the active issue platform. Supported values are `youtrack`, `jira`, `github`, `gitlab`, and `bitbucket`. |
| `OPENHANDS_AGENT_TICKET_SYSTEM` | Backward-compatible alias for `OPENHANDS_AGENT_ISSUE_PLATFORM`. |
| `YOUTRACK_BASE_URL` | YouTrack API base URL. |
| `YOUTRACK_TOKEN` | YouTrack API token. |
| `YOUTRACK_PROJECT` | YouTrack project key used to fetch tasks. |
| `YOUTRACK_ASSIGNEE` | YouTrack assignee to scan for tasks. |
| `YOUTRACK_PROGRESS_STATE_FIELD` | YouTrack field used for the in-progress transition. |
| `YOUTRACK_PROGRESS_STATE` | YouTrack value used for the in-progress transition. |
| `YOUTRACK_REVIEW_STATE_FIELD` | YouTrack field used for the review transition. |
| `YOUTRACK_REVIEW_STATE` | YouTrack value used for the review transition. |
| `YOUTRACK_ISSUE_STATES` | YouTrack issue states that qualify for processing. |
| `JIRA_BASE_URL` | Jira API base URL. |
| `JIRA_TOKEN` | Jira API token. |
| `JIRA_EMAIL` | Jira user email for authentication. |
| `JIRA_PROJECT` | Jira project key used to fetch tasks. |
| `JIRA_ASSIGNEE` | Jira assignee to scan for tasks. |
| `JIRA_PROGRESS_STATE_FIELD` | Jira field used for the in-progress transition. |
| `JIRA_PROGRESS_STATE` | Jira value used for the in-progress transition. |
| `JIRA_REVIEW_STATE_FIELD` | Jira field used for the review transition. |
| `JIRA_REVIEW_STATE` | Jira value used for the review transition. |
| `JIRA_ISSUE_STATES` | Jira issue states that qualify for processing. |
| `GITHUB_ISSUES_BASE_URL` | GitHub Issues API base URL. |
| `GITHUB_API_TOKEN` | GitHub API token. Also used for GitHub git push and PR creation when needed. |
| `GITHUB_ISSUES_OWNER` | GitHub repository owner used to scope issues. |
| `GITHUB_ISSUES_REPO` | GitHub repository name used to scope issues. |
| `GITHUB_ISSUES_ASSIGNEE` | GitHub assignee to scan for tasks. |
| `GITHUB_ISSUES_PROGRESS_STATE_FIELD` | GitHub Issues field used for the in-progress transition. |
| `GITHUB_ISSUES_PROGRESS_STATE` | GitHub Issues value used for the in-progress transition. |
| `GITHUB_ISSUES_REVIEW_STATE_FIELD` | GitHub Issues field used for the review transition. |
| `GITHUB_ISSUES_REVIEW_STATE` | GitHub Issues value used for the review transition. |
| `GITHUB_ISSUES_ISSUE_STATES` | GitHub Issues states that qualify for processing. |
| `GITLAB_ISSUES_BASE_URL` | GitLab Issues API base URL. |
| `GITLAB_API_TOKEN` | GitLab API token. Also used for GitLab git push and merge request creation when needed. |
| `GITLAB_ISSUES_PROJECT` | GitLab project path used to scope issues. |
| `GITLAB_ISSUES_ASSIGNEE` | GitLab assignee to scan for tasks. |
| `GITLAB_ISSUES_PROGRESS_STATE_FIELD` | GitLab Issues field used for the in-progress transition. |
| `GITLAB_ISSUES_PROGRESS_STATE` | GitLab Issues value used for the in-progress transition. |
| `GITLAB_ISSUES_REVIEW_STATE_FIELD` | GitLab Issues field used for the review transition. |
| `GITLAB_ISSUES_REVIEW_STATE` | GitLab Issues value used for the review transition. |
| `GITLAB_ISSUES_ISSUE_STATES` | GitLab Issues states that qualify for processing. |
| `BITBUCKET_ISSUES_BASE_URL` | Bitbucket Issues API base URL. |
| `BITBUCKET_API_TOKEN` | Bitbucket API token. Also used as the password for Bitbucket Basic auth when `BITBUCKET_USERNAME` is set, and for Bitbucket git push and pull request creation when needed. |
| `BITBUCKET_USERNAME` | Bitbucket username used with `BITBUCKET_API_TOKEN` for Basic auth. Leave empty to keep the legacy token-only flow. |
| `BITBUCKET_ISSUES_WORKSPACE` | Bitbucket workspace used to scope issues. |
| `BITBUCKET_ISSUES_REPO_SLUG` | Bitbucket repository slug used to scope issues. |
| `BITBUCKET_ISSUES_ASSIGNEE` | Bitbucket assignee to scan for tasks. |
| `BITBUCKET_ISSUES_PROGRESS_STATE_FIELD` | Bitbucket Issues field used for the in-progress transition. |
| `BITBUCKET_ISSUES_PROGRESS_STATE` | Bitbucket Issues value used for the in-progress transition. |
| `BITBUCKET_ISSUES_REVIEW_STATE_FIELD` | Bitbucket Issues field used for the review transition. |
| `BITBUCKET_ISSUES_REVIEW_STATE` | Bitbucket Issues value used for the review transition. |
| `BITBUCKET_ISSUES_ISSUE_STATES` | Bitbucket Issues states that qualify for processing. |
| `REPOSITORY_ROOT_PATH` | Root folder where the agent scans for checked-out repositories. |
| `OPENHANDS_AGENT_IGNORED_REPOSITORY_FOLDERS` | Comma-separated folder names to exclude from repository auto-discovery. |
| `OPENHANDS_AGENT_DB_PROTOCOL` | Database protocol used by the agent persistence layer. |
| `OPENHANDS_AGENT_DB_USERNAME` | Database username when a non-SQLite backend needs it. |
| `OPENHANDS_AGENT_DB_PASSWORD` | Database password when a non-SQLite backend needs it. |
| `OPENHANDS_AGENT_DB_HOST` | Database host when a non-SQLite backend needs it. |
| `OPENHANDS_AGENT_DB_PORT` | Database port when a non-SQLite backend needs it. |
| `OPENHANDS_AGENT_DB_PATH` | Database path or directory when the backend uses one. |
| `OPENHANDS_AGENT_DB_FILE` | SQLite database filename. |

### OpenHands Agent Runtime

| Variable | What it does |
| --- | --- |
| `OPENHANDS_BASE_URL` | Base URL for the primary OpenHands server. |
| `OPENHANDS_API_KEY` | API key for the primary OpenHands server. |
| `OPENHANDS_SKIP_TESTING` | Skips the testing validation conversation and publishes after implementation. |
| `OPENHANDS_TESTING_CONTAINER_ENABLED` | Enables the optional dedicated testing OpenHands container. |
| `OPENHANDS_TESTING_BASE_URL` | Base URL for the dedicated testing OpenHands server. |
| `OPENHANDS_TESTING_PORT` | Host port used for the optional testing container. |
| `OPENHANDS_CONTAINER_LOG_ALL_EVENTS` | Enables all OpenHands event logging inside the `openhands` container. |
| `OPENHANDS_AGENT_MAX_RETRIES` | Retry count for external API calls. |
| `OPENHANDS_AGENT_LOG_LEVEL` | Log level for the agent app process. |
| `OPENHANDS_AGENT_WORKFLOW_LOG_LEVEL` | Log level for workflow-specific logs. |
| `OPENHANDS_POLL_INTERVAL_SECONDS` | Delay between OpenHands conversation polling attempts. |
| `OPENHANDS_MAX_POLL_ATTEMPTS` | Maximum number of times the agent waits for an OpenHands conversation result. |
| `OPENHANDS_TASK_SCAN_STARTUP_DELAY_SECONDS` | Delay before the agent starts scanning for tasks after startup. |
| `OPENHANDS_TASK_SCAN_INTERVAL_SECONDS` | Delay between task scan cycles. |
| `OPENHANDS_AGENT_FAILURE_EMAIL_ENABLED` | Enables failure notification emails. |
| `OPENHANDS_AGENT_FAILURE_EMAIL_TEMPLATE_ID` | Template id used for failure notification emails. |
| `OPENHANDS_AGENT_FAILURE_EMAIL_TO` | Recipient address for failure notification emails. |
| `OPENHANDS_AGENT_FAILURE_EMAIL_SENDER_NAME` | Sender name for failure notification emails. |
| `OPENHANDS_AGENT_FAILURE_EMAIL_SENDER_EMAIL` | Sender email for failure notification emails. |
| `OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED` | Enables completion notification emails. |
| `OPENHANDS_AGENT_COMPLETION_EMAIL_TEMPLATE_ID` | Template id used for completion notification emails. |
| `OPENHANDS_AGENT_COMPLETION_EMAIL_TO` | Recipient address for completion notification emails. |
| `OPENHANDS_AGENT_COMPLETION_EMAIL_SENDER_NAME` | Sender name for completion notification emails. |
| `OPENHANDS_AGENT_COMPLETION_EMAIL_SENDER_EMAIL` | Sender email for completion notification emails. |
| `EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY` | SendInBlue API key used by `email-core-lib`. |
| `SLACK_WEBHOOK_URL_ERRORS_EMAIL` | Slack webhook used by `email-core-lib` error reporting. |

The `openhands` container reuses the same `OPENHANDS_LLM_*` and `AWS_*` values from the shared `.env` file, so the Bedrock configuration is defined once. `OPENHANDS_CONTAINER_LOG_ALL_EVENTS` is the only service-specific override for that container.

### OpenHands Container

| Variable | What it does |
| --- | --- |
| `OPENHANDS_PORT` | Host port exposed for the OpenHands container. |
| `OPENHANDS_LOG_LEVEL` | OpenHands container log level. |
| `OH_SECRET_KEY` | OpenHands secret key used to persist secrets safely across restarts. |
| `OPENHANDS_STATE_DIR` | Host path for OpenHands state storage. |
| `OPENHANDS_WEB_URL` | Public URL that OpenHands should advertise. |
| `OPENHANDS_RUNTIME` | Runtime backend used by OpenHands. |
| `OPENHANDS_AGENT_SERVER_IMAGE_REPOSITORY` | Agent server image repository used by the OpenHands container. |
| `OPENHANDS_AGENT_SERVER_IMAGE_TAG` | Agent server image tag used by the OpenHands container. |
| `OPENHANDS_SSH_AUTH_SOCK_HOST_PATH` | Host SSH agent socket path forwarded into Docker for SSH git remotes. |

### OpenHands LLM

| Variable | What it does |
| --- | --- |
| `OPENHANDS_LLM_MODEL` | Primary OpenHands model name. |
| `OPENHANDS_LLM_API_KEY` | API key for the primary OpenHands model. |
| `OPENHANDS_LLM_BASE_URL` | Optional custom base URL for the primary OpenHands model. |
| `OPENHANDS_MODEL_SMOKE_TEST_ENABLED` | Runs a small model smoke test during connection validation. |
| `OPENHANDS_TESTING_LLM_MODEL` | Model name used by the dedicated testing OpenHands server. |
| `OPENHANDS_TESTING_LLM_API_KEY` | API key used by the dedicated testing OpenHands server. |
| `OPENHANDS_TESTING_LLM_BASE_URL` | Base URL used by the dedicated testing OpenHands server. |
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

For Bedrock specifically, you can use either standard AWS credentials or a Bedrock bearer token.

The active issue provider comes from `openhands_agent.issue_platform`, which falls back to `openhands_agent.ticket_system`, and finally defaults to `youtrack`.
Issue states can be configured directly in `.env` with `YOUTRACK_ISSUE_STATES`, `JIRA_ISSUE_STATES`, `GITHUB_ISSUES_ISSUE_STATES`, `GITLAB_ISSUES_ISSUE_STATES`, and `BITBUCKET_ISSUES_ISSUE_STATES`.
The review-state target also comes from the active provider config:
- YouTrack uses `openhands_agent.youtrack.review_state_field` and `openhands_agent.youtrack.review_state`.
- Jira uses `openhands_agent.jira.review_state_field` and `openhands_agent.jira.review_state`.
- GitHub Issues uses `openhands_agent.github_issues.review_state_field` and `openhands_agent.github_issues.review_state`.
- GitLab Issues uses `openhands_agent.gitlab_issues.review_state_field` and `openhands_agent.gitlab_issues.review_state`.
- Bitbucket Issues uses `openhands_agent.bitbucket_issues.review_state_field` and `openhands_agent.bitbucket_issues.review_state`.
Processed task state, processed review-comment ids, and pull-request comment context are kept in memory during a run so the agent can skip already-completed work and poll for new review comments without writing local state.
If email notifications are enabled, install the optional dependency set with `python -m pip install -e ".[notifications]"`.
The email body text comes from [`completion_email.txt`](openhands_agent/templates/email/completion_email.txt) and [`failure_email.txt`](openhands_agent/templates/email/failure_email.txt), rendered with template variables at runtime.
The Hydra config is registered through [`hydra_plugins/openhands_agent/openhands_agent_searchpath.py`](hydra_plugins/openhands_agent/openhands_agent_searchpath.py), so standard Hydra overrides work. Example:

```bash
python -m openhands_agent.main openhands_agent.retry.max_retries=7
```

### Open Source Notes

This project is meant to be usable by other teams, so a few things are worth calling out up front:

- `make configure` is the easiest way to create a first `.env`, and `.env.example` is the canonical template.
- Never commit real secrets. Keep `.env` local, and only use `.env.example` for documentation and defaults.
- The workflow is split on purpose:
  - OpenHands edits files in the task branch.
  - orchestration handles commit, push, pull request creation, and branch restoration.
- Testing behavior is controlled by separate flags:
  - `OPENHANDS_SKIP_TESTING=true` skips the validation conversation entirely.
  - `OPENHANDS_TESTING_CONTAINER_ENABLED=true` enables the dedicated testing OpenHands container.
  - `OPENHANDS_MODEL_SMOKE_TEST_ENABLED=false` only disables the startup smoke test.
- If you change `.env`, recreate the containers so Docker Compose reloads the new values.
- Bedrock-backed models may need `AWS_SESSION_TOKEN` in addition to the AWS access key and secret when temporary credentials are used.
- SSH git remotes require `SSH_AUTH_SOCK` to be mounted correctly.
- `clean.sh` is destructive and is intended for local cleanup only. It removes Docker containers and prunes unused Docker resources without prompting.

### Troubleshooting

If something does not work as expected, the most common checks are:

1. Run `docker compose config` and confirm the rendered values match the working configuration.
2. Recreate the containers after changing `.env`.
3. Confirm the repository workspace is on the destination branch after a failure or after cleanup.
4. Check whether the active issue platform and repository provider are both configured in `.env`.
5. Verify that the OpenHands model credentials match the provider you selected.

Common failure modes:

- Bedrock authentication errors usually mean the AWS key, secret, region, or session token is wrong or stale.
- Branch-publish failures usually mean the task branch never got a committed change or the repo could not be restored cleanly.
- Dirty worktree errors mean the task branch still has uncommitted edits and the workspace needs cleanup before the next run.
- Missing git permissions usually mean the host repository path or SSH auth socket is not mounted the way the container expects.

### Supported Providers

The agent currently supports these issue trackers:

- YouTrack
- Jira
- GitHub Issues
- GitLab Issues
- Bitbucket Issues

The repository provider is inferred from the configured repository metadata, and the same task can span multiple repositories if the task text matches them.

## How To Use

### Full First-Run Checklist

If a developer is starting from zero, these are the steps:

1. Clone the repository.
2. Change into the repository directory.
3. Run `make bootstrap`.
4. Run `make configure` to create `.env`, or copy `.env.example` to `.env` and edit it manually.
5. Fill in or confirm the credentials for the selected issue platform.
6. Fill in or confirm the first repository entry credentials and local path.
7. Add more repository entries in the config file if tasks can span multiple repos.
8. Fill in or confirm OpenHands server settings.
9. Fill in or confirm OpenHands LLM provider settings.
10. Fill in email settings if notifications are enabled.
11. Decide whether to run locally or with Docker Compose.
12. Validate the environment values.
13. Create or upgrade the database schema.
14. Start the application.
15. Confirm the agent can connect to the configured issue platform, OpenHands, and every configured repository.

What is automated now:

- `./scripts/bootstrap.sh`
  - creates `.env` from `.env.example` if needed
  - creates `.venv` if needed
  - installs the project
  - runs the tests
- `make configure`
  - asks which issue platform holds your tasks
  - asks which platform hosts your code
  - can scan a projects folder for git repositories
  - asks which issue states and review state should be used
  - writes `.env` for the root repository path and OpenHands setup
- `make doctor`
  - validates agent and OpenHands env vars
  - exits non-zero if required values are missing, so it can be used in CI or pre-flight scripts
- `make run`
  - loads `.env`
  - starts the app
- `make install`
  - runs `OpenHandsAgentCoreLib.install`
  - applies the Alembic migrations to the configured database
- Docker entrypoint
  - waits for OpenHands
  - starts the app

Still manual:

- filling real secrets in `.env`
- choosing the LLM/provider/model
- choosing whether to use local run or Docker
- adding extra repository entries directly in YAML when a task can span multiple repositories

### Quick Commands

1. Bootstrap the repo:

```bash
make bootstrap
```

2. Create `.env` interactively:

```bash
make configure
```

3. Validate config:

```bash
make doctor
```

`make doctor` returns a non-zero exit code on validation failure.

4. Run locally:

```bash
make run
```

5. Install or upgrade the database schema:

```bash
make install
```

6. Or run with Docker:

```bash
make compose-up
```

### Manual Flow

1. Install the project dependencies in your environment.

```bash
pip install -e .
```

2. Fill `.env` instead of exporting variables one by one. Start from `.env.example` and update the values you need there.

3. Adjust `openhands_agent/config/openhands_agent_core_lib.yaml` only if you need settings beyond what `.env` exposes, such as extra repositories. Issue states, review columns, and review-ready email recipients can now be configured directly in `.env`.

```yaml
openhands_agent:
  issue_platform: youtrack
  ticket_system: youtrack
  retry:
    max_retries: 5
  failure_email:
    enabled: true
    template_id: "42"
    body_template: failure_email.txt
    recipients:
      - ops@example.com
  completion_email:
    enabled: true
    template_id: "77"
    body_template: completion_email.txt
    recipients:
      - reviewers@example.com
  youtrack:
    review_state_field: State
    review_state: In Review
    issue_states:
      - Todo
      - Open
  jira:
    review_state_field: status
    review_state: In Review
    issue_states:
      - To Do
      - Open
  github_issues:
    review_state_field: labels
    review_state: In Review
    issue_states:
      - open
  gitlab_issues:
    review_state_field: labels
    review_state: In Review
    issue_states:
      - opened
  bitbucket_issues:
    review_state_field: state
    review_state: resolved
    issue_states:
      - new
      - open
```

4. Load `.env` in your shell and run the agent.

```bash
set -a
source .env
set +a
python -m openhands_agent.install
python -m openhands_agent.main
```

### Docker Compose

You can also run OpenHands and this agent together with Docker Compose:

```bash
docker compose up --build
```

What the compose stack does:

- starts an `openhands` container on port `3000`
- runs an `install` container that calls `OpenHandsAgentCoreLib.install`
- builds and starts an `openhands-agent` container from this repo
- shares the default SQLite database path between `install` and `openhands-agent`
- makes the agent wait until OpenHands is reachable at `http://openhands:3000`
- then runs `python -m openhands_agent.main`

The compose file uses the current official OpenHands container image pattern from the OpenHands docs:

- https://docs.openhands.dev/openhands/usage/run-openhands/local-setup
- https://github.com/OpenHands/OpenHands

Before running `docker compose up --build`, make sure `.env` contains the selected issue-platform settings, repository settings, OpenHands settings, retry settings, and optional email settings you want Docker Compose to pass through.
Docker Compose uses `REPOSITORY_ROOT_PATH` as the host source path and mounts it into both the agent container and the OpenHands sandbox at `/workspace/project`, so Docker runs use the same in-container workspace path consistently. The agent mount must stay writable because the agent itself performs git preflight, branch checkout, and fast-forward pulls there before delegating implementation work.
For the default SQLite setup, the compose file stores the database under `data/` in the agent container working directory, backed by a named Docker volume shared by the `install` and `openhands-agent` containers. If you use Postgres or another external database, override `OPENHANDS_AGENT_DB_PATH` and the related DB env vars in `.env`.

If you use `.env`, Docker Compose will load it automatically, so you can keep both the agent config and the OpenHands LLM config in one place and avoid manual setup in the OpenHands UI for the env-supported options. The `openhands` service also reads its logging and model defaults from the same file.
The OpenHands container always stores its internal state at `/.openhands`; `OPENHANDS_STATE_DIR` only controls which host folder is mounted there, so prefer an absolute host path when overriding it.

OpenHands behavioral rules are also supported from this repo through [`AGENTS.md`](AGENTS.md). That lets you keep coding/testing instructions in the project instead of configuring them manually in OpenHands.

What happens when it runs:

- It fetches only tasks assigned to the configured issue-platform assignee.
- It ignores tasks that are not in the configured `issue_states`.
- It enriches the task context with issue comments, text attachment contents, and screenshot attachment references when the selected platform exposes them.
- It retries transient client failures up to `openhands_agent.retry.max_retries`.
- If the overall run fails, it sends failure notifications through `email-core-lib` to the configured recipients.
- For each eligible task, it infers the affected repositories, asks OpenHands to implement the work across that scoped workspace set, opens one pull request per repository, comments the aggregated PR summary back to the configured issue platform, moves the issue to the configured review state when all repositories succeed, and sends a completion email that asks for review.
- After task processing, it polls tracked pull requests for new review comments, passes the full accumulated review-comment context into OpenHands for each unseen comment, and records processed comment ids so the same comment is not reprocessed on the next run.

### Partial Failure Behavior

If a task spans multiple repositories and one pull request succeeds while another fails, the agent does not roll back the successful pull request. Instead it:

- posts the partial pull-request summary back to the configured issue platform
- records the failed repository ids in the run result
- leaves the issue out of the review state transition
- sends the failure notification path with the failing repositories in the error text

That behavior is deliberate: the agent prefers explicit partial visibility over trying to revert repository state automatically.

## Testing

From the repository root, install the project in your environment and run the unit test suite with:

```bash
pip install -e .
python3 -m unittest discover -s tests
```

If you only want to run a single test module, use:

```bash
python3 -m unittest discover -s tests -p 'test_notification_service.py'
```

## What This Scaffold Implements

- `core-lib` application wrapper for the agent.
- `core-lib`-style `client`, `data_layers/data`, `data_layers/data_access`, and `data_layers/service` packages.
- Data-access wrappers around issue platforms, OpenHands, and repository provider integrations.
- A service layer that orchestrates the full task-to-PR flow.
- A webhook-style handler for pull-request review comments.
- A job entrypoint for processing assigned tasks plus a `tests/config` Hydra scaffold.

## Current Limitations

- Real git workspace handling per task.
- Authentication/signature verification for webhooks.
- Final adaptation to the exact OpenHands API and your issue-platform fields.
- No end-to-end integration test exercises a live issue-platform -> OpenHands -> pull-request provider flow yet.

## Environment Variables Configuration

We use a `.env` file to manage configuration instead of hardcoding values in `docker-compose.yaml`. To set up your environment:

1. Copy the example environment file:
```bash
cp .env.example .env
```

2. Configure your `.env` file with your actual values:
```bash
# Edit .env file with your specific configurations for:
# - Issue platform credentials (YouTrack, Jira, GitHub, GitLab, Bitbucket)
# - Database settings
# - OpenHands configuration
# - LLM settings (including AWS credentials for Bedrock models if needed)
# - Sandbox volumes mapping
```


# Saving costs tips

Use a cheaper main OPENHANDS_LLM_MODEL. This is usually the largest lever.
Lower OPENHANDS_AGENT_MAX_RETRIES from 5 to 2 or 3 if your setup is stable.
Keep YOUTRACK_ISSUE_STATES tight so only truly ready tasks get processed.
Batch review feedback into fewer comments, because each review-fix cycle can trigger more OpenHands work.
Keep task context lean: avoid huge pasted logs, long comment threads, and unnecessary attachments.
Keep task and review-comment handling lean so the in-memory workflow stays predictable during a run.
Don’t expect much savings from poll interval tuning; that mostly affects waiting/API chatter, not LLM spend.
