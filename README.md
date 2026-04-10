<p align="center">
  <img src="./kato.png" alt="Kato" width="220" />
</p>

# Kato

Welcome to Kato! This repository is structured as a [`core-lib`](https://shay-te.github.io/core-lib/) application and follows the documented `core-lib` package layout.

## Why Kato

The name comes from Kato, the Green Hornet's sidekick, famously played by Bruce Lee. That makes it a fitting name for this project: a helper that works alongside the main mission, stays useful in the background, and helps get important work done.

I love and respect Bruce Lee, and I wanted the name to reflect that admiration.

## Why Core-Lib

`core-lib` is a strong fit for this project because this agent is not just a script that calls one API. It has to coordinate issue platforms, repository providers, OpenHands, jobs, configuration, persistence, notifications, and testing in one place without collapsing into one large pile of glue code.

Why it works especially well here:

- `core-lib` gives the project a clean layered shape: clients for external APIs, data-access wrappers for boundaries, services for orchestration, and jobs for entrypoints. That maps directly to what this agent does every day.
- `core-lib` is built around a central application library object, which is exactly what this project needs. `KatoCoreLib` can be initialized once and reused from the CLI, scheduled jobs, and tests instead of rebuilding the application's wiring in multiple places.
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
   `kato.issue_platform` defaults to `youtrack` when unset.
   Only tasks assigned to the configured assignee and currently in one of the configured `issue_states` are eligible.
   When loading a task, the agent also reads issue comments, text attachments, and screenshot attachment metadata so OpenHands gets more complete context.
2. Read each task definition.
3. Ask OpenHands to implement the required changes.
4. Create one pull request per affected repository.
5. Add the aggregated pull request summary back to the configured issue platform, move the issue to the configured review state, and send a review-ready email.
6. Listen to pull request comments and trigger follow-up fixes.

## Structure

```text
kato/
  client/
    bitbucket_issues_client.py
    bitbucket_client.py
    github_issues_client.py
    gitlab_issues_client.py
    jira_client.py
    kato_client.py
    ticket_client_base.py
    ticket_client_factory.py
    youtrack_client.py
  config/
    kato_core_lib.yaml
  templates/
    email/
      completion_email.j2
      failure_email.j2
  data_layers/
    data/
    data_access/
      pull_request_data_access.py
      task_data_access.py
    service/
      agent_service.py
      implementation_service.py
  jobs/
    process_assigned_tasks.py
  main.py
  kato_core_lib.py
  kato_instance.py
scripts/
  generate_env.py
tests/
  config/
    config.yaml
```

## How It Works

This project follows the `core-lib` layering on purpose:

- `KatoCoreLib` wires the app once at startup, builds the clients, data-access objects, and services, and validates the external connections before work starts.
- `client/` contains provider-specific API code for issue platforms, repository providers, and OpenHands.
- `data_layers/data_access/` stays focused on boundary work such as ticket updates and pull-request API calls.
- `data_layers/service/` owns the business workflow. This is where task selection, state transitions, repository preparation, OpenHands runs, publishing, notifications, and review-comment handling live.

That separation matters because the service flow should read like the real agent workflow. Kato starts by validating configuration and external access, then repeats one scan loop: process assigned tasks first, then process pull-request review comments. Tasks and comments are processed sequentially, one after the other, so repository state from one item does not leak into the next one.

### Highlight Summary

- Startup validates `.env`, repository access, the active issue platform, the main OpenHands server, and the testing OpenHands server unless testing is skipped.
- The scan loop waits for the configured startup delay, scans assigned tasks, then scans review comments, then sleeps until the next scan.
- The task-fix flow reads the task, prepares clean branches, opens OpenHands implementation and testing conversations, commits and pushes changes, opens pull requests, moves the task to review, and stores pull-request context for follow-up comments.
- The review-comment fix flow scans review pull requests, skips already-handled comment threads, opens an OpenHands review-fix conversation, pushes the branch update, replies to the reviewer, resolves the comment when supported, and records the processed comment keys.
- Failed repository, branch, push, publish, and state-transition checks stop the unsafe part of the workflow instead of marking work as done too early.

### Startup Flow

1. `python -m kato.main`, `make run`, or the Docker entrypoint loads Hydra config and values from `.env`.
2. Environment validation runs before the application is built. Missing required values fail fast.
3. `KatoCoreLib` builds the active issue-platform client, repository service, OpenHands implementation service, OpenHands testing service, notification service, task publisher, preflight service, and review-comment service.
4. Startup dependency validation checks repository connections, the active issue-platform connection, the main OpenHands connection, and the testing OpenHands connection unless `OPENHANDS_SKIP_TESTING=true`.
5. After startup succeeds, the job loop waits for `OPENHANDS_TASK_SCAN_STARTUP_DELAY_SECONDS`.
6. Each loop cycle runs task processing first and review-comment processing second.
7. If a cycle fails, the error is logged and the loop retries after `OPENHANDS_TASK_SCAN_INTERVAL_SECONDS`.

### Task Fix Flow

For each eligible assigned task, the service does these checks and steps:

1. Skip the task if it was already processed during this run.
2. Validate model access for the task before spending work on repository changes.
3. Check whether an earlier blocking comment still prevents a retry.
4. Read the full task context, including issue comments, supported text attachments, and screenshot attachment metadata.
5. Infer the affected repositories from the task summary and description.
6. Validate that every repository is available locally, on the expected destination branch, and clean before starting work.
7. Build the task branch name for each repository and prepare those branches locally.
8. Before OpenHands starts, fetch `origin` and rebase any existing local task branch on top of `origin/<branch>` when that remote branch exists.
9. Validate that task branches can be pushed.
10. Move the issue to the in-progress state and add a started comment.
11. Open the implementation conversation in the main OpenHands server.
12. Validate that the task branches contain publishable changes.
13. Open the testing conversation in the configured testing OpenHands server, or skip it when `OPENHANDS_SKIP_TESTING=true`.
14. Commit and push the branch updates, then create pull requests or merge requests through the repository provider API.
15. Add the pull-request summary back to the task.
16. If every repository published successfully, move the task to the configured review state, mark the task processed for this run, and send the completion notification.
17. Remember the pull-request context so later review comments can be mapped back to the correct repository, branch, task, and OpenHands session.

If any repository cannot be published, the successful pull requests are kept, the task is not moved to the review state, and the failure is reported clearly instead of being hidden.

### Review Comment Fix Flow

After task processing, the agent checks tracked review pull requests for unseen comments:

0. Before polling comments, compare the current review-state task list against all tasks with tracked pull-request contexts. For any task that is no longer in the review state (merged, moved to done, or closed by the reviewer), Kato deletes its OpenHands conversation so the agent-server container is stopped and removed. On normal process shutdown (SIGTERM / SIGINT), all remaining conversations are also deleted.
1. Look only at pull requests that belong to tasks already moved into the review state.
2. Load or reconstruct the saved pull-request context for the repository, branch, task, and OpenHands session.
3. Fetch pull-request comments from the repository provider.
4. Build the full review-comment thread context for OpenHands.
5. Skip comment threads already replied to by Kato, already processed in memory, or already covered by another comment with the same resolution target.
6. Log `Working on pull request comments: <pull request name>` before logging the concrete comment id.
7. Prepare the same working branch again by fetching `origin` and rebasing the local branch on `origin/<branch>` before the review-fix conversation starts.
8. Open the review-fix conversation in OpenHands with the pull request comment and the saved task context. The saved session ID from the original implementation conversation is passed as the parent so the agent-server container is reused for context and cost efficiency.
9. Publish the review fix back to the same branch. If git push is still rejected because the remote branch changed while OpenHands was working, Kato fetches `origin/<branch>`, rebases once, and retries the push.
10. Reply to the original review comment with the OpenHands result.
11. Resolve the review comment when the provider supports it.
12. If the provider reports the comment is already resolved or unavailable, Kato logs a warning and continues because the fix was already published and replied.
13. Mark both the visible comment id and the provider resolution target as processed so the same thread is not handled again in the same run.
14. If the review-comment flow fails, restore repository branches before the failure is raised.

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

Use `KATO_ISSUE_PLATFORM` for all new setups.

## Third-Party Setup

Pick one issue platform with `KATO_ISSUE_PLATFORM`, then fill in the matching block below. Keep the other issue-platform blocks empty unless you are switching providers or using their repository API credentials for pull requests.

After editing `.env`, run:

```bash
make doctor
```

### Setting Up YouTrack

Use this when tasks are coming from YouTrack:

```env
KATO_ISSUE_PLATFORM=youtrack
YOUTRACK_BASE_URL=https://your-company.youtrack.cloud
YOUTRACK_TOKEN=...
YOUTRACK_PROJECT=PROJ
YOUTRACK_ASSIGNEE=your-youtrack-login
YOUTRACK_ISSUE_STATES=Todo,Open
YOUTRACK_PROGRESS_STATE_FIELD=State
YOUTRACK_PROGRESS_STATE=In Progress
YOUTRACK_REVIEW_STATE_FIELD=State
YOUTRACK_REVIEW_STATE=To Verify
```

`YOUTRACK_ISSUE_STATES` is the queue Kato scans. The progress and review state settings tell Kato how to move the issue when work starts and when the pull request is ready.

### Setting Up Jira

Use this when tasks are coming from Jira:

```env
KATO_ISSUE_PLATFORM=jira
JIRA_BASE_URL=https://your-company.atlassian.net
JIRA_TOKEN=...
JIRA_EMAIL=you@example.com
JIRA_PROJECT=PROJ
JIRA_ASSIGNEE=assignee-account-id-or-username
JIRA_ISSUE_STATES=To Do,Open
JIRA_PROGRESS_STATE_FIELD=status
JIRA_PROGRESS_STATE=In Progress
JIRA_REVIEW_STATE_FIELD=status
JIRA_REVIEW_STATE=In Review
```

`JIRA_TOKEN` is the API token. Keep `JIRA_EMAIL` set for Atlassian authentication flows that need the account email.

### Setting Up GitHub Issues

Use this when tasks are coming from GitHub Issues:

```env
KATO_ISSUE_PLATFORM=github
GITHUB_ISSUES_BASE_URL=https://api.github.com
GITHUB_API_TOKEN=...
GITHUB_ISSUES_OWNER=owner-or-org
GITHUB_ISSUES_REPO=repo-name
GITHUB_ISSUES_ASSIGNEE=assignee-login
GITHUB_ISSUES_ISSUE_STATES=open
GITHUB_ISSUES_PROGRESS_STATE_FIELD=labels
GITHUB_ISSUES_PROGRESS_STATE=In Progress
GITHUB_ISSUES_REVIEW_STATE_FIELD=labels
GITHUB_ISSUES_REVIEW_STATE=In Review
```

`GITHUB_API_TOKEN` is also used for GitHub git push and pull request creation when discovered repositories live on GitHub.

### Setting Up GitLab Issues

Use this when tasks are coming from GitLab Issues:

```env
KATO_ISSUE_PLATFORM=gitlab
GITLAB_ISSUES_BASE_URL=https://gitlab.com/api/v4
GITLAB_API_TOKEN=...
GITLAB_ISSUES_PROJECT=group/project
GITLAB_ISSUES_ASSIGNEE=assignee-username
GITLAB_ISSUES_ISSUE_STATES=opened
GITLAB_ISSUES_PROGRESS_STATE_FIELD=labels
GITLAB_ISSUES_PROGRESS_STATE=In Progress
GITLAB_ISSUES_REVIEW_STATE_FIELD=labels
GITLAB_ISSUES_REVIEW_STATE=In Review
```

`GITLAB_API_TOKEN` is also used for GitLab git push and merge request creation when discovered repositories live on GitLab.

### Setting Up Bitbucket Issues

Use this when tasks are coming from Bitbucket Issues:

```env
KATO_ISSUE_PLATFORM=bitbucket
BITBUCKET_ISSUES_BASE_URL=https://api.bitbucket.org/2.0
BITBUCKET_API_TOKEN=...
BITBUCKET_USERNAME=bitbucket-username
BITBUCKET_API_EMAIL=you@example.com
BITBUCKET_ISSUES_WORKSPACE=workspace
BITBUCKET_ISSUES_REPO_SLUG=repo-slug
BITBUCKET_ISSUES_ASSIGNEE=assignee-username
BITBUCKET_ISSUES_ISSUE_STATES=new,open
BITBUCKET_ISSUES_PROGRESS_STATE_FIELD=state
BITBUCKET_ISSUES_PROGRESS_STATE=open
BITBUCKET_ISSUES_REVIEW_STATE_FIELD=state
BITBUCKET_ISSUES_REVIEW_STATE=resolved
```

`BITBUCKET_API_TOKEN` is used for Bitbucket git auth and REST API calls. `BITBUCKET_API_EMAIL` is required for Bitbucket pull request API auth.

### Setting Up OpenHands With Bedrock

Use this when `OPENHANDS_LLM_MODEL` starts with `bedrock/`:

```env
OH_SECRET_KEY=stable-random-local-secret
OPENHANDS_LLM_MODEL=bedrock/your-model-id
OPENHANDS_LLM_API_KEY=
OPENHANDS_LLM_BASE_URL=
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION_NAME=us-west-2
AWS_SESSION_TOKEN=
AWS_BEARER_TOKEN_BEDROCK=
```

For Bedrock auth, choose one path:

- Standard AWS credentials: set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_REGION_NAME`. Set `AWS_SESSION_TOKEN` too when the credentials are temporary. Leave `AWS_BEARER_TOKEN_BEDROCK` empty.
- Bedrock bearer token: set `AWS_BEARER_TOKEN_BEDROCK`. Leave `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION_NAME`, and `AWS_SESSION_TOKEN` empty.

### Setting Up OpenHands With OpenRouter

Use this when `OPENHANDS_LLM_MODEL` starts with `openrouter/`:

```env
OH_SECRET_KEY=stable-random-local-secret
OPENHANDS_LLM_MODEL=openrouter/openai/gpt-4o-mini
OPENHANDS_LLM_API_KEY=...
OPENHANDS_LLM_BASE_URL=https://openrouter.ai/api/v1
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION_NAME=
AWS_SESSION_TOKEN=
AWS_BEARER_TOKEN_BEDROCK=
```

OpenRouter requires both `OPENHANDS_LLM_API_KEY` and `OPENHANDS_LLM_BASE_URL`. Leave the AWS Bedrock variables empty for OpenRouter runs.

## Environment Reference

The list below mirrors `.env.example`.

### Ticket And Repository

| Variable | What it does |
| --- | --- |
| `KATO_ISSUE_PLATFORM` | Selects the active issue platform. Supported values are `youtrack`, `jira`, `github`, `gitlab`, and `bitbucket`. |
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
| `BITBUCKET_API_TOKEN` | Bitbucket API token. Used as the password for Bitbucket git auth and Bitbucket REST API auth. |
| `BITBUCKET_USERNAME` | Bitbucket username used for git push auth. |
| `BITBUCKET_API_EMAIL` | Atlassian account email used for Bitbucket REST API auth with API tokens. |
| `BITBUCKET_ISSUES_WORKSPACE` | Bitbucket workspace used to scope issues. |
| `BITBUCKET_ISSUES_REPO_SLUG` | Bitbucket repository slug used to scope issues. |
| `BITBUCKET_ISSUES_ASSIGNEE` | Bitbucket assignee to scan for tasks. |
| `BITBUCKET_ISSUES_PROGRESS_STATE_FIELD` | Bitbucket Issues field used for the in-progress transition. |
| `BITBUCKET_ISSUES_PROGRESS_STATE` | Bitbucket Issues value used for the in-progress transition. |
| `BITBUCKET_ISSUES_REVIEW_STATE_FIELD` | Bitbucket Issues field used for the review transition. |
| `BITBUCKET_ISSUES_REVIEW_STATE` | Bitbucket Issues value used for the review transition. |
| `BITBUCKET_ISSUES_ISSUE_STATES` | Bitbucket Issues states that qualify for processing. |
| `REPOSITORY_ROOT_PATH` | Root folder where the agent scans for checked-out repositories. |
| `MOUNT_DOCKER_DATA_ROOT` | Host folder that holds all Docker bind-mounted data under one parent directory. |
| `KATO_IGNORED_REPOSITORY_FOLDERS` | Comma-separated folder names to exclude from repository auto-discovery. |

### Kato Runtime

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

### OpenHands Container

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

### OpenHands LLM

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

The active issue provider comes from `kato.issue_platform`, which defaults to `youtrack`.
Issue states can be configured directly in `.env` with `YOUTRACK_ISSUE_STATES`, `JIRA_ISSUE_STATES`, `GITHUB_ISSUES_ISSUE_STATES`, `GITLAB_ISSUES_ISSUE_STATES`, and `BITBUCKET_ISSUES_ISSUE_STATES`.
The review-state target also comes from the active provider config:
- YouTrack uses `kato.youtrack.review_state_field` and `kato.youtrack.review_state`.
- Jira uses `kato.jira.review_state_field` and `kato.jira.review_state`.
- GitHub Issues uses `kato.github_issues.review_state_field` and `kato.github_issues.review_state`.
- GitLab Issues uses `kato.gitlab_issues.review_state_field` and `kato.gitlab_issues.review_state`.
- Bitbucket Issues uses `kato.bitbucket_issues.review_state_field` and `kato.bitbucket_issues.review_state`.
Processed task state, processed review-comment ids, and pull-request comment context are kept in memory during a run so the agent can skip already-completed work and poll for new review comments without writing local state.
If email notifications are enabled, install the optional dependency set with `python -m pip install -e ".[notifications]"`.
The email body text comes from [`completion_email.j2`](kato/templates/email/completion_email.j2) and [`failure_email.j2`](kato/templates/email/failure_email.j2), rendered with Jinja2 template variables at runtime.
The Hydra config is registered through [`hydra_plugins/kato/kato_searchpath.py`](hydra_plugins/kato/kato_searchpath.py), so standard Hydra overrides work. Example:

```bash
python -m kato.main kato.retry.max_retries=7
```

### Open Source Notes

This project is meant to be usable by other teams, so a few things are worth calling out up front:

- `make configure` is the easiest way to create a first `.env`, and `.env.example` is the canonical template.
- Never commit real secrets. Keep `.env` local, and only use `.env.example` for documentation and defaults.
- The workflow is split on purpose:
  - OpenHands edits files in the task branch.
  - orchestration handles commit, push, pull request creation, and branch restoration.
- Before task work starts, the agent runs a model-access preflight for the configured OpenHands model(s), so invalid Bedrock or OpenRouter credentials fail fast before implementation begins.
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
13. Start the application.
14. Confirm the agent can connect to the configured issue platform, OpenHands, and every configured repository.

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

5. Or run with Docker:

```bash
make compose-up
```

### Manual Flow

1. Install the project dependencies in your environment.

```bash
pip install -e .
```

2. Fill `.env` instead of exporting variables one by one. Start from `.env.example` and update the values you need there.

3. Adjust `kato/config/kato_core_lib.yaml` only if you need settings beyond what `.env` exposes, such as extra repositories or retry tuning via `KATO_EXTERNAL_API_MAX_RETRIES`. Issue states, review columns, and review-ready email recipients can now be configured directly in `.env`.

```yaml
kato:
  issue_platform: youtrack
  retry:
    max_retries: 5
  failure_email:
    enabled: true
    template_id: "42"
    body_template: failure_email.j2
    recipients:
      - ops@example.com
  completion_email:
    enabled: true
    template_id: "77"
    body_template: completion_email.j2
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
    base_url: https://api.bitbucket.org/2.0
    token: BITBUCKET_API_TOKEN
    username: BITBUCKET_USERNAME
    api_email: BITBUCKET_API_EMAIL
    workspace: your-workspace
    repo_slug: issues-repo
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
python -m kato.main
```

### Docker Compose

You can also run OpenHands and this agent together with Docker Compose:

```bash
docker compose up --build
```

What the compose stack does:

- starts an `openhands` container on port `3000`
- builds and starts an `kato` container from this repo
- makes the agent wait until OpenHands is reachable at `http://openhands:3000`
- then runs `python -m kato.main`

The compose file uses the current official OpenHands container image pattern from the OpenHands docs:

- https://docs.openhands.dev/openhands/usage/run-openhands/local-setup
- https://github.com/OpenHands/OpenHands

Before running `docker compose up --build`, make sure `.env` contains the selected issue-platform settings, repository settings, OpenHands settings, retry settings, and optional email settings you want Docker Compose to pass through.
Docker Compose uses `REPOSITORY_ROOT_PATH` as the host source path and mounts it into both the agent container and the OpenHands sandbox at `/workspace/project`, so Docker runs use the same in-container workspace path consistently. The agent mount must stay writable because the agent itself performs git preflight, branch checkout, and fast-forward pulls there before delegating implementation work.
All Docker bind-mounted runtime data lives under `MOUNT_DOCKER_DATA_ROOT` (default `./mount_docker_data`) in service-specific subfolders such as `openhands/` and `openhands_state/`.

If you use `.env`, Docker Compose will load it automatically, so you can keep both the agent config and the OpenHands LLM config in one place and avoid manual setup in the OpenHands UI for the env-supported options. The `openhands` service also reads its logging and model defaults from the same file.
The OpenHands container always stores its internal state at `/.openhands`; `OPENHANDS_STATE_DIR` only controls which host folder is mounted there, so prefer an absolute host path when overriding it. By default, the host side lives under `MOUNT_DOCKER_DATA_ROOT/openhands_state/`.

OpenHands behavioral rules are also supported from this repo through [`AGENTS.md`](AGENTS.md). That lets you keep coding/testing instructions in the project instead of configuring them manually in OpenHands.

What happens when it runs:

- It fetches only tasks assigned to the configured issue-platform assignee.
- It ignores tasks that are not in the configured `issue_states`.
- It enriches the task context with issue comments, text attachment contents, and screenshot attachment references when the selected platform exposes them.
- It retries transient client failures up to `kato.retry.max_retries`.
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

The test suite includes:

- mocked unit tests for the orchestration services, especially `agent_service`, `implementation_service`, `repository_service`, and `testing_service`
- boundary tests for the provider clients and retry helpers
- small integration-style regressions that exercise the task-to-PR workflow shape without hitting live external systems

CI runs the same suite under `coverage` and prints a coverage summary in the job log.

If you only want to run a single test module, use:

```bash
python3 -m unittest discover -s tests -p 'test_notification_service.py'
```

## What This Scaffold Implements

- `core-lib` application wrapper for the agent.
- `core-lib`-style `client`, `data_layers/data`, `data_layers/data_access`, and `data_layers/service` packages.
- Data-access wrappers around issue platforms, OpenHands, and repository provider integrations.
- A service layer that orchestrates the full task-to-PR flow.
- A review-comment processing loop for pull-request review comments.
- A job entrypoint for processing assigned tasks plus a `tests/config` Hydra scaffold.

## Current Limitations

- Real git workspace handling per task.
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
Lower `kato.retry.max_retries` from 3 to 2 or 3 if your setup is stable.
Keep YOUTRACK_ISSUE_STATES tight so only truly ready tasks get processed.
Batch review feedback into fewer comments, because each review-fix cycle can trigger more OpenHands work.
Keep task context lean: avoid huge pasted logs, long comment threads, and unnecessary attachments.
Keep task and review-comment handling lean so the in-memory workflow stays predictable during a run.
Don’t expect much savings from poll interval tuning; that mostly affects waiting/API chatter, not LLM spend.
