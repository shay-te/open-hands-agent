# How to Use — kato

End-to-end usage: setup, run, Docker, partial-failure behavior, and operator notes. For a shorter setup walkthrough see [SETUP.md](SETUP.md).

## Required Environment

For the shortest local setup path, use the interactive configurator:

```bash
kato bootstrap
kato configure
kato doctor
kato up
```

`kato configure` runs `python scripts/generate_env.py --output .env` and writes a first-pass `.env` for you. It asks:

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

## Full First-Run Checklist

If a developer is starting from zero, these are the steps:

1. Clone the repository.
2. Change into the repository directory.
3. Run `kato bootstrap`.
4. Run `kato configure` to create `.env`, or copy `.env.example` to `.env` and edit it manually.
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
- `kato configure`
  - asks which issue platform holds your tasks
  - asks which platform hosts your code
  - can scan a projects folder for git repositories
  - asks which issue states and review state should be used
  - writes `.env` for the root repository path and OpenHands setup
- `kato doctor`
  - validates agent and OpenHands env vars
  - exits non-zero if required values are missing, so it can be used in CI or pre-flight scripts
- `kato up`
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

## Quick Commands

1. Bootstrap the repo:

```bash
kato bootstrap
```

2. Create `.env` interactively:

```bash
kato configure
```

3. Validate config:

```bash
kato doctor
```

`kato doctor` returns a non-zero exit code on validation failure.

4. Run locally:

```bash
kato up
```

5. Or run with Docker:

```bash
kato compose-docker
```

`kato compose-docker` brings the Compose stack up in the background and then attaches
directly to the `kato` container TTY, so inline countdowns and rotating status
spinners render in place instead of being flattened into prefixed Compose log lines.

## Manual Flow

1. Install the project dependencies in your environment.

```bash
pip install -e .
```

2. Fill `.env` instead of exporting variables one by one. Start from `.env.example` and update the values you need there.

3. Adjust `kato_core_lib/config/kato_core_lib.yaml` only if you need settings beyond what `.env` exposes, such as extra repositories or retry tuning via `KATO_EXTERNAL_API_MAX_RETRIES`. Issue states, review columns, and review-ready email recipients can now be configured directly in `.env`.

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
python -m kato_core_lib.main
```

## Docker Compose

You can also run OpenHands and this agent together with Docker Compose:

```bash
docker compose up --build
```

If you want the Kato inline spinner and countdown UI to render correctly, prefer
`kato compose-docker` over raw `docker compose up --build`, because the Make target
attaches directly to the `kato` container terminal.

What the compose stack does:

- starts an `openhands` container on port `3000`
- builds and starts an `kato` container from this repo
- makes the agent wait until OpenHands is reachable at `http://openhands:3000`
- then runs `python -m kato_core_lib.main`

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

## Partial Failure Behavior

If a task spans multiple repositories and one pull request succeeds while another fails, the agent does not roll back the successful pull request. Instead it:

- posts the partial pull-request summary back to the configured issue platform
- records the failed repository ids in the run result
- leaves the issue out of the review state transition
- sends the failure notification path with the failing repositories in the error text

That behavior is deliberate: the agent prefers explicit partial visibility over trying to revert repository state automatically.

## Open Source Notes

This project is meant to be usable by other teams, so a few things are worth calling out up front:

- `kato configure` is the easiest way to create a first `.env`, and `.env.example` is the canonical template.
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
