# OpenHands YouTrack Agent

This repository is structured as a [`core-lib`](https://github.com/shay-te/core-lib) application and follows the documented `core-lib` package layout.

The agent is designed to:

1. Read tasks assigned to it from YouTrack.
   Only tasks assigned to the configured assignee and currently in one of the configured `issue_states` are eligible.
   When loading a task, the agent also reads issue comments, text attachments, and screenshot attachment metadata so OpenHands gets more complete context.
2. Read each task definition.
3. Ask OpenHands to implement the required changes.
4. Create a pull request in Bitbucket.
5. Add the pull request link back to YouTrack, move the issue to the configured review state, and send a review-ready email.
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

Then fill in the values you need. The most important variables are:

```bash
export YOUTRACK_BASE_URL="https://your-company.youtrack.cloud"
export YOUTRACK_TOKEN="..."
export YOUTRACK_PROJECT="PROJ"
export YOUTRACK_ASSIGNEE="me"
export BITBUCKET_BASE_URL="https://api.bitbucket.org/2.0"
export BITBUCKET_TOKEN="..."
export BITBUCKET_WORKSPACE="your-workspace"
export BITBUCKET_REPO_SLUG="your-repo"
export OPENHANDS_BASE_URL="http://localhost:3000"
export OPENHANDS_API_KEY="..."
export OPENHANDS_AGENT_MAX_RETRIES="5"
export OPENHANDS_AGENT_FAILURE_EMAIL_ENABLED="true"
export OPENHANDS_AGENT_FAILURE_EMAIL_TEMPLATE_ID="42"
export OPENHANDS_AGENT_FAILURE_EMAIL_TO="ops@example.com"
export OPENHANDS_AGENT_FAILURE_EMAIL_SENDER_NAME="OpenHands Agent"
export OPENHANDS_AGENT_FAILURE_EMAIL_SENDER_EMAIL="noreply@example.com"
export OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED="true"
export OPENHANDS_AGENT_COMPLETION_EMAIL_TEMPLATE_ID="77"
export OPENHANDS_AGENT_COMPLETION_EMAIL_TO="reviewers@example.com"
export OPENHANDS_AGENT_COMPLETION_EMAIL_SENDER_NAME="OpenHands Agent"
export OPENHANDS_AGENT_COMPLETION_EMAIL_SENDER_EMAIL="noreply@example.com"
export YOUTRACK_REVIEW_STATE_FIELD="State"
export YOUTRACK_REVIEW_STATE="In Review"
export EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY="..."
export SLACK_WEBHOOK_URL_ERRORS_EMAIL=""
```

OpenHands itself can now be configured from this project too. Put its LLM settings in `.env` and `docker compose` will pass them into the `openhands` container:

```bash
export OPENHANDS_LLM_MODEL="openai/gpt-4o"
export OPENHANDS_LLM_API_KEY="..."
```

Optional advanced OpenHands settings supported by this compose file:

```bash
export OPENHANDS_LLM_BASE_URL=""
export OPENHANDS_LLM_API_VERSION=""
export OPENHANDS_LLM_NUM_RETRIES=""
export OPENHANDS_LLM_TIMEOUT=""
export OPENHANDS_LLM_DISABLE_VISION=""
export OPENHANDS_LLM_DROP_PARAMS=""
export OPENHANDS_LLM_CACHING_PROMPT=""
export AWS_ACCESS_KEY_ID=""
export AWS_SECRET_ACCESS_KEY=""
export AWS_REGION_NAME=""
export AWS_BEARER_TOKEN_BEDROCK=""
```

For Bedrock specifically, you can use either standard AWS credentials or a Bedrock bearer token, all from env vars before OpenHands starts:

```bash
export OPENHANDS_LLM_MODEL="bedrock/anthropic.claude-3-sonnet-20240229-v1:0"
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION_NAME="us-west-2"
```

or:

```bash
export OPENHANDS_LLM_MODEL="bedrock/anthropic.claude-3-sonnet-20240229-v1:0"
export AWS_BEARER_TOKEN_BEDROCK="..."
```

Allowed YouTrack stages are configured in `openhands_agent/config/openhands_agent_core_lib.yaml` under `openhands_agent.youtrack.issue_states`. By default the agent only processes tasks assigned to `YOUTRACK_ASSIGNEE` that are in `Todo` or `Open`.
The target YouTrack review column is configured with `openhands_agent.youtrack.review_state_field` and `openhands_agent.youtrack.review_state`. By default the agent moves completed issues to `State=In Review`.
Retry count is configured under `openhands_agent.retry.max_retries`.
Failure emails are configured under `openhands_agent.failure_email` and sent through `email-core-lib`.
Completion emails are configured under `openhands_agent.completion_email` and sent through `email-core-lib`.
The email body text comes from [completion_email.txt](/Users/shaytessler/Desktop/dev/openhands-agent/openhands_agent/templates/email/completion_email.txt) and [failure_email.txt](/Users/shaytessler/Desktop/dev/openhands-agent/openhands_agent/templates/email/failure_email.txt), rendered with template variables at runtime.

## How To Use

1. Install the project dependencies in your environment.

```bash
pip install -e .
```

2. Export the required environment variables.

```bash
export YOUTRACK_BASE_URL="https://your-company.youtrack.cloud"
export YOUTRACK_TOKEN="..."
export YOUTRACK_PROJECT="PROJ"
export YOUTRACK_ASSIGNEE="me"
export BITBUCKET_BASE_URL="https://api.bitbucket.org/2.0"
export BITBUCKET_TOKEN="..."
export BITBUCKET_WORKSPACE="your-workspace"
export BITBUCKET_REPO_SLUG="your-repo"
export OPENHANDS_BASE_URL="http://localhost:3000"
export OPENHANDS_API_KEY="..."
export YOUTRACK_REVIEW_STATE_FIELD="State"
export YOUTRACK_REVIEW_STATE="In Review"
```

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

4. Run the agent.

```bash
python -m openhands_agent.main
```

If you need to create or upgrade the database schema first, run:

```bash
python -m openhands_agent.create_db
```

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

Before running `docker compose up --build`, export the same environment variables listed above for YouTrack, Bitbucket, OpenHands, retries, and failure email settings.

If you use `.env`, Docker Compose will load it automatically, so you can keep both the agent config and the OpenHands LLM config in one place and avoid manual setup in the OpenHands UI for the env-supported options.

OpenHands behavioral rules are also supported from this repo through [AGENTS.md](/Users/shaytessler/Desktop/dev/openhands-agent/AGENTS.md). That lets you keep coding/testing instructions in the project instead of configuring them manually in OpenHands.

What happens when it runs:

- It fetches only tasks assigned to `YOUTRACK_ASSIGNEE`.
- It ignores tasks that are not in the configured `issue_states`.
- It enriches the task context with YouTrack comments, text attachment contents, and screenshot attachment references.
- It retries transient client failures up to `openhands_agent.retry.max_retries`.
- If the overall run fails, it sends failure notifications through `email-core-lib` to the configured recipients.
- For each eligible task, it asks OpenHands to implement the work, opens a Bitbucket pull request, comments the PR URL back to YouTrack, moves the issue to the configured review state, and sends a completion email that asks for review.

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
- Data-access wrappers around YouTrack, OpenHands, and Bitbucket integrations.
- A service layer that orchestrates the full task-to-PR flow.
- A webhook-style handler for Bitbucket PR comments.
- A job entrypoint for processing assigned tasks plus a `tests/config` Hydra scaffold.

## What Still Needs Completion

- Real git workspace handling per task.
- Authentication/signature verification for webhooks.
- Persistent storage for processed tasks and PR mappings.
- Final adaptation to the exact OpenHands API and your YouTrack fields.
