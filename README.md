# OpenHands YouTrack Agent

This repository is structured as a [`core-lib`](https://github.com/shay-te/core-lib) application and follows the documented `core-lib` package layout.

The agent is designed to:

1. Read tasks assigned to it from YouTrack.
2. Read each task definition.
3. Ask OpenHands to implement the required changes.
4. Create a pull request in Bitbucket.
5. Listen to pull request comments and trigger follow-up fixes.

## Structure

```text
openhands_agent/
  client/
    bitbucket_client.py
    openhands_client.py
    youtrack_client.py
  config/
    core_lib.yaml
  data_layers/
    data/
      review_comment.py
      task.py
    data_access/
      implementation_data_access.py
      pull_request_data_access.py
      task_data_access.py
    service/
      agent_service.py
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
export YOUTRACK_BASE_URL="https://your-company.youtrack.cloud"
export YOUTRACK_TOKEN="..."
export YOUTRACK_PROJECT="PROJ"
export BITBUCKET_BASE_URL="https://api.bitbucket.org/2.0"
export BITBUCKET_TOKEN="..."
export BITBUCKET_WORKSPACE="your-workspace"
export BITBUCKET_REPO_SLUG="your-repo"
export OPENHANDS_BASE_URL="http://localhost:3000"
export OPENHANDS_API_KEY="..."
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
