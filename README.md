# OpenHands YouTrack Agent

This repository is structured as a [`core-lib`](https://github.com/shay-te/core-lib) application and follows the documented `core-lib` package layout.

The agent is designed to:

1. Read tasks assigned to it from YouTrack.
   Only tasks assigned to the configured assignee and currently in one of the configured `issue_states` are eligible.
   When loading a task, the agent also reads issue comments, text attachments, and screenshot attachment metadata so OpenHands gets more complete context.
2. Read each task definition.
3. Ask OpenHands to implement the required changes.
4. Create one pull request per affected repository.
5. Add the aggregated pull request summary back to YouTrack, move the issue to the configured review state, and send a review-ready email.
6. Listen to pull request comments and trigger follow-up fixes.

## Structure

```text
openhands_agent/
  client/
    bitbucket_client.py
    openhands_client.py
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
tests/
  config/
    config.yaml
```

## Required Environment

```bash
cp .env.example .env
```

For the shortest local setup path, use:

```bash
make bootstrap
# fill .env
make doctor
make run
```

Then fill in `.env`. The most important entries are:

```dotenv
YOUTRACK_BASE_URL=https://your-company.youtrack.cloud
YOUTRACK_TOKEN=...
YOUTRACK_PROJECT=PROJ
YOUTRACK_ASSIGNEE=your-youtrack-login
REPOSITORY_ID=client
REPOSITORY_DISPLAY_NAME=Client
REPOSITORY_LOCAL_PATH=./client
REPOSITORY_BASE_URL=https://api.bitbucket.org/2.0
REPOSITORY_TOKEN=...
REPOSITORY_OWNER=your-workspace
REPOSITORY_REPO_SLUG=your-repo
REPOSITORY_DESTINATION_BRANCH=
OPENHANDS_BASE_URL=http://localhost:3000
OPENHANDS_API_KEY=...
OPENHANDS_AGENT_MAX_RETRIES=5
OPENHANDS_AGENT_FAILURE_EMAIL_ENABLED=true
OPENHANDS_AGENT_FAILURE_EMAIL_TEMPLATE_ID=42
OPENHANDS_AGENT_FAILURE_EMAIL_TO=ops@example.com
OPENHANDS_AGENT_FAILURE_EMAIL_SENDER_NAME=OpenHands Agent
OPENHANDS_AGENT_FAILURE_EMAIL_SENDER_EMAIL=noreply@example.com
OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED=true
OPENHANDS_AGENT_COMPLETION_EMAIL_TEMPLATE_ID=77
OPENHANDS_AGENT_COMPLETION_EMAIL_TO=reviewers@example.com
OPENHANDS_AGENT_COMPLETION_EMAIL_SENDER_NAME=OpenHands Agent
OPENHANDS_AGENT_COMPLETION_EMAIL_SENDER_EMAIL=noreply@example.com
YOUTRACK_REVIEW_STATE_FIELD=State
YOUTRACK_REVIEW_STATE=In Review
EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY=...
SLACK_WEBHOOK_URL_ERRORS_EMAIL=
```

The pull-request provider is selected automatically from `REPOSITORY_BASE_URL`.
Supported providers:
- Bitbucket: `https://api.bitbucket.org/2.0`
- GitHub: `https://api.github.com`
- GitLab: `https://gitlab.com/api/v4`

For multi-repository tasks, add more entries under `openhands_agent.repositories` in `openhands_agent/config/openhands_agent_core_lib.yaml`. Each repository entry needs `id`, `display_name`, `local_path`, `provider_base_url`, `token`, `owner`, `repo_slug`, and optional `destination_branch` plus `aliases`.
The flat `REPOSITORY_*` environment variables are only a bootstrap convenience for the first repository entry. Once you need more than one repository, treat `openhands_agent/config/openhands_agent_core_lib.yaml` as the source of truth and add the extra entries there.

If `destination_branch` is empty, the agent infers the repository default branch from the local git checkout. That is convenient for local development, but it also means runtime behavior depends on the checkout state. For production-style runs, set `destination_branch` explicitly for every repository so pull requests cannot target the wrong branch because of a stale or unusual local clone.

OpenHands itself can now be configured from this project too. Put its LLM settings in `.env` and `docker compose` will pass them into the `openhands` container:

```dotenv
OPENHANDS_LLM_MODEL=openai/gpt-4o
OPENHANDS_LLM_API_KEY=...
```

Optional advanced OpenHands settings supported by this compose file:

```dotenv
OPENHANDS_LLM_BASE_URL=
OPENHANDS_LLM_API_VERSION=
OPENHANDS_LLM_NUM_RETRIES=
OPENHANDS_LLM_TIMEOUT=
OPENHANDS_LLM_DISABLE_VISION=
OPENHANDS_LLM_DROP_PARAMS=
OPENHANDS_LLM_CACHING_PROMPT=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION_NAME=
AWS_BEARER_TOKEN_BEDROCK=
```

For Bedrock specifically, you can use either standard AWS credentials or a Bedrock bearer token, all from env vars before OpenHands starts:

```dotenv
OPENHANDS_LLM_MODEL=bedrock/anthropic.claude-3-sonnet-20240229-v1:0
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION_NAME=us-west-2
```

or:

```dotenv
OPENHANDS_LLM_MODEL=bedrock/anthropic.claude-3-sonnet-20240229-v1:0
AWS_BEARER_TOKEN_BEDROCK=...
```

Allowed YouTrack stages are configured in `openhands_agent/config/openhands_agent_core_lib.yaml` under `openhands_agent.youtrack.issue_states`. By default the agent only processes tasks assigned to `YOUTRACK_ASSIGNEE` that are in `Todo` or `Open`.
The target YouTrack review column is configured with `openhands_agent.youtrack.review_state_field` and `openhands_agent.youtrack.review_state`. By default the agent moves completed issues to `State=In Review`.
Retry count is configured under `openhands_agent.retry.max_retries`.
Processed task state, processed review-comment ids, and pull-request comment context are persisted in `OPENHANDS_AGENT_STATE_FILE` so the agent can skip already-completed work, poll for new review comments, and still resolve review comments after a restart.
Failure emails are configured under `openhands_agent.failure_email` and sent through `email-core-lib`.
Completion emails are configured under `openhands_agent.completion_email` and sent through `email-core-lib`.
If email notifications are enabled, install the optional dependency set with `python -m pip install -e ".[notifications]"`.
The email body text comes from [`completion_email.txt`](openhands_agent/templates/email/completion_email.txt) and [`failure_email.txt`](openhands_agent/templates/email/failure_email.txt), rendered with template variables at runtime.
The Hydra config is registered through [`hydra_plugins/openhands_agent/openhands_agent_searchpath.py`](hydra_plugins/openhands_agent/openhands_agent_searchpath.py), so standard Hydra overrides work. Example:

```bash
python -m openhands_agent.main openhands_agent.retry.max_retries=7
```

## How To Use

### Full First-Run Checklist

If a developer is starting from zero, these are the steps:

1. Clone the repository.
2. Change into the repository directory.
3. Copy `.env.example` to `.env`.
4. Fill in YouTrack credentials.
5. Fill in the first repository entry credentials and local path.
6. Add more repository entries in the config file if tasks can span multiple repos.
7. Fill in OpenHands server settings.
8. Fill in OpenHands LLM provider settings.
9. Fill in email settings if notifications are enabled.
10. Decide whether to run locally or with Docker Compose.
11. Create a virtual environment for local development.
12. Install the package in editable mode.
13. Run the test suite.
14. Validate the environment values.
15. Create or upgrade the database schema.
16. Start the application.
17. Confirm the agent can connect to YouTrack, OpenHands, and every configured repository.

What is automated now:

- `./scripts/bootstrap.sh`
  - creates `.env` from `.env.example` if needed
  - creates `.venv` if needed
  - installs the project
  - runs the tests
- `make doctor`
  - validates agent and OpenHands env vars
  - exits non-zero if required values are missing, so it can be used in CI or pre-flight scripts
- `make run`
  - loads `.env`
  - starts the app
- Docker entrypoint
  - waits for OpenHands
  - starts the app
- Startup
  - creates the SQLAlchemy schema automatically because `core_lib.data.sqlalchemy.create_db` is enabled

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

2. Fill `.env`

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

3. Adjust `openhands_agent/config/openhands_agent_core_lib.yaml` if you want different allowed YouTrack stages, a different review column, or review-ready email recipients.

```yaml
openhands_agent:
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
```

4. Load `.env` in your shell and run the agent.

```bash
set -a
source .env
set +a
python -m openhands_agent.main
```

If you need to create or upgrade the database schema first, run:

```bash
python -m openhands_agent.create_db
```

Normal startup already creates the configured SQLAlchemy tables automatically because `core_lib.data.sqlalchemy.create_db` is set to `true`.

### Docker Compose

You can also run OpenHands and this agent together with Docker Compose:

```bash
docker compose up --build
```

What the compose stack does:

- starts an `openhands` container on port `3000`
- builds and starts an `openhands-agent` container from this repo
- makes the agent wait until OpenHands is reachable at `http://openhands:3000`
- then runs `python -m openhands_agent.main`

The compose file uses the official OpenHands image and runtime image pattern from the OpenHands docs:

- https://docs.all-hands.dev/usage/local-setup
- https://github.com/OpenHands/OpenHands

Before running `docker compose up --build`, make sure `.env` contains the YouTrack, repository, OpenHands, retry, and optional email settings you want Docker Compose to pass through.

If you use `.env`, Docker Compose will load it automatically, so you can keep both the agent config and the OpenHands LLM config in one place and avoid manual setup in the OpenHands UI for the env-supported options.

OpenHands behavioral rules are also supported from this repo through [`AGENTS.md`](AGENTS.md). That lets you keep coding/testing instructions in the project instead of configuring them manually in OpenHands.

What happens when it runs:

- It fetches only tasks assigned to `YOUTRACK_ASSIGNEE`.
- It ignores tasks that are not in the configured `issue_states`.
- It enriches the task context with YouTrack comments, text attachment contents, and screenshot attachment references.
- It retries transient client failures up to `openhands_agent.retry.max_retries`.
- If the overall run fails, it sends failure notifications through `email-core-lib` to the configured recipients.
- For each eligible task, it infers the affected repositories, asks OpenHands to implement the work across that scoped workspace set, opens one pull request per repository, comments the aggregated PR summary back to YouTrack, moves the issue to the configured review state when all repositories succeed, and sends a completion email that asks for review.
- After task processing, it polls tracked pull requests for new review comments, passes the full accumulated review-comment context into OpenHands for each unseen comment, and records processed comment ids so the same comment is not reprocessed on the next run.

### Partial Failure Behavior

If a task spans multiple repositories and one pull request succeeds while another fails, the agent does not roll back the successful pull request. Instead it:

- posts the partial pull-request summary back to YouTrack
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
- Data-access wrappers around YouTrack, OpenHands, and repository provider integrations.
- A service layer that orchestrates the full task-to-PR flow.
- A webhook-style handler for pull-request review comments.
- A job entrypoint for processing assigned tasks plus a `tests/config` Hydra scaffold.

## Current Limitations

- Real git workspace handling per task.
- Authentication/signature verification for webhooks.
- Final adaptation to the exact OpenHands API and your YouTrack fields.
- No end-to-end integration test exercises a live YouTrack -> OpenHands -> pull-request provider flow yet.
