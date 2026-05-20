# Architecture — kato

How kato is built, why it's built that way, and what happens when it runs. For an even deeper architecture dive see [architecture.md](architecture.md).

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

## Core-lib map

Kato is a thin orchestrator on top of a stack of focused libraries. Each one has a clear responsibility and either implements a contract (provider impls) or wraps several providers behind a typed factory (wrapping libs).

```
   ┌─────────────────────────────────────────────────────────────────────┐
   │                                                                     │
   │                          kato_core_lib                              │
   │                                                                     │
   │  product orchestrator: assigned-task scan → REP gate → workspace    │
   │  clone → preflight → planning session → publish → review-fix loop   │
   │                                                                     │
   │  webserver/   planning UI (Flask + React) lives here                │
   │                                                                     │
   └──┬──────────────┬───────────────────┬──────────────────┬────────────┘
      │              │                   │                  │
      │ wraps        │ wraps             │ wraps            │ uses
      ▼              ▼                   ▼                  ▼

  ┌─────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐
  │ task_   │  │ repository_  │  │ agent_       │  │ sandbox_       │
  │ core_lib│  │ core_lib     │  │ core_lib     │  │ core_lib       │
  │         │  │              │  │              │  │                │
  │ factory │  │ factory +    │  │ factory +    │  │ INDEPENDENT    │
  │ +       │  │ provider     │  │ AgentPlatform│  │ (no contracts, │
  │ Platform│  │ routing      │  │ enum         │  │  no providers, │
  │ enum    │  │              │  │              │  │  self-contained)│
  └────┬────┘  └──────┬───────┘  └──────┬───────┘  │                │
       │              │                 │          │ Dockerfile +   │
       │ depends on   │ depends on      │ depends  │ tls_pin +      │
       ▼              ▼                 │ on       │ audit_log +    │
  ┌────────────────────────────┐ ┌─────────────────────┐│ workspace_  │
  │ vcs_provider_contracts     │ │ agent_provider_      ││ delimiter + │
  │                            │ │ contracts            ││ credential_ │
  │  IssueProvider     (ABC)   │ │                      ││ patterns +  │
  │  PullRequestProvider (ABC) │ │  AgentProvider       ││ bypass_     │
  │  DTOs: Issue, PullRequest, │ │   (Protocol)         ││ permissions │
  │  ReviewComment,            │ │  DTOs: AgentTask,    ││ _validator  │
  │  IssueComment              │ │   AgentReviewComment,│└────────────────┘
  │                            │ │   AgentResult        │
  │  pure ABCs + DTOs, no impl │ │   pure ABCs + DTOs   │
  └────────────────────────────┘ └─────────────────────┘
       ▲              ▲                 ▲
       │ implements   │ implements      │ implements
       │              │                 │
   ┌───┴───┐      ┌───┴────┐      ┌─────┴──────┬───────────────┐
   │       │      │        │      │            │               │
   │       │      │        │      │            │               │
youtrack jira_  github_  gitlab_  claude_   openhands_     codex_
_core_   core   core_lib core_lib core_lib  core_lib       core_lib
 lib    _lib                                                (future)

                bitbucket_
                core_lib                  subprocess     HTTP/RPC      subprocess
                                          + NDJSON       no stream     + NDJSON
                                          stream                       stream
```

Five layers, top to bottom:

1. **`kato_core_lib`** — the product. Orchestration loop + planning UI. Calls the four wrapping libs through their typed contracts; never reaches into a provider directly.
2. **Wrapping factory libs** — `task_core_lib`, `repository_core_lib`, `agent_core_lib`, plus `sandbox_core_lib` (the odd one out — see below). Each is a thin factory + Platform enum. No business logic.
3. **Contracts packages** — `vcs_provider_contracts` and `agent_provider_contracts`. Pure `Protocol` + frozen DTOs. Zero implementation, zero dependencies on anything else in the repo. Implementations import from contracts; contracts import from nothing.
4. **Provider implementations** — one per concrete backend. `youtrack_core_lib`, `jira_core_lib`, `github_core_lib`, `gitlab_core_lib`, `bitbucket_core_lib` for VCS/issues; `claude_core_lib`, `openhands_core_lib` (and future `codex_core_lib`) for agents. Each implements one or more contracts.
5. **Independent units** — `sandbox_core_lib` is the only one today. It's a flat self-contained library, not a contracts/factory/provider triangle, because the domain doesn't have alternatives — there's only one sandbox model (hardened-Docker-for-CLI-agents).

Adding a new provider (e.g. a `gerrit_core_lib` for code review, or a `codex_core_lib` for the OpenAI Codex agent) follows the same playbook every time: implement the contract, wire it into the factory, add a Platform enum value. Kato itself doesn't change.

## Structure

```text
kato_core_lib/
  client/                        # external services kato talks to
    agent_client.py              # AgentClient Protocol — the contract
    retrying_client_base.py      # shared retry / HTTP plumbing
    pull_request_client_*.py     # cross-provider PR abstraction
    ticket_client_*.py           # cross-provider issue abstraction
    bitbucket/                   # Bitbucket auth + PR + issues
    github/                      # GitHub PR + issues
    gitlab/                      # GitLab PR + issues
    jira/                        # Jira issues
    youtrack/                    # YouTrack issues
    claude/                      # Claude Code CLI backend
      cli_client.py              #   one-shot autonomous client
      streaming_session.py       #   long-lived planning subprocess
      session_manager.py         #   per-task session registry + persistence
    openhands/                   # OpenHands HTTP backend (kato_client.py)
    openrouter/                  # OpenRouter helpers (used by openhands)
  config/
    kato_core_lib.yaml
  data_layers/
    data/                        # YouTrack / git / agent value types
    data_access/                 # raw fetch + parse layer
    service/                     # orchestration: scan → plan → execute
      agent_service.py           #   top-level loop, tag handling
      task_preflight_service.py  #   resolve repos, prep branches
      task_publisher.py          #   commit, push, open PR
      planning_session_runner.py #   route to streaming Claude
      review_comment_service.py  #   handle PR review feedback
  helpers/                       # cross-cutting *_utils.py modules
  validation/                    # startup + per-task safety checks
  jobs/process_assigned_tasks.py # the cron-scheduled scan loop
  main.py                        # process entrypoint
  kato_core_lib.py               # core-lib wiring: builds AgentService
webserver/                       # planning UI (Flask + React)
  kato_webserver/
    app.py                       #   Flask routes (SSE + POST)
    git_diff_utils.py            #   tree / diff for the right pane
    session_registry.py          #   in-memory tab list (legacy)
  templates/index.html           # HTML shell
  static/css/app.css             # dark-theme styles
  static/js/app.js               # vanilla-JS chat + SSE + status bar
  ui/                            # Vite + React source for the right pane
    src/{App,FilesTab,ChangesTab}.jsx
scripts/
  bootstrap.sh                   # Mac/Linux first-time setup
  bootstrap.ps1                  # Windows PowerShell equivalent
tests/
  config/config.yaml             # test fixture config
```

### Architecture at a glance

```text
                       ┌──────────────────────────────┐
                       │  YouTrack / Jira / GitHub …  │
                       │   (issues, comments, tags)   │
                       └──────────────┬───────────────┘
                                      │ poll
                                      ▼
                  ┌────────────────────────────────────┐
                  │  kato.main  ─  ProcessAssignedTasks │
                  │   30s scan loop, signal handling   │
                  └──────────────┬─────────────────────┘
                                 │
                                 ▼
              ┌──────────────────────────────────────────┐
              │              AgentService                │
              │  • wait-planning short-circuit (chat tab)│
              │  • TaskPreflightService (resolve+prep)   │
              │  • runner OR one-shot client (implement) │
              │  • TestingService (validate)             │
              │  • TaskPublisher (commit / push / PR)    │
              └──────────────┬───────────────────────────┘
                             │
                ┌────────────┴────────────┐
                ▼                         ▼
   ┌─────────────────────┐   ┌────────────────────────────┐
   │  ClaudeCliClient    │   │     KatoClient (OpenHands) │
   │  (one-shot -p)      │   │     HTTP API client        │
   └──────────┬──────────┘   └────────────────────────────┘
              │ also used by:
              ▼
   ┌─────────────────────────────────────────────────────┐
   │           PlanningSessionRunner                     │
   │  uses ClaudeSessionManager + StreamingClaudeSession │
   │  (long-lived `claude -p --input-format stream-json` │
   │   subprocess, one per task id, persisted records)   │
   └──────────┬──────────────────────────────────────────┘
              │ shared in-memory ↕ persisted on disk
              ▼
   ┌─────────────────────────────────────────────────────┐
   │      Planning UI webserver (daemon thread)          │
   │  Flask + SSE  →  vanilla JS  +  React right-pane    │
   │  • tab list, chat, permission modal                 │
   │  • Files / Changes tabs (git tree + diff)           │
   │  • status bar (kato logger → ring buffer → SSE)     │
   │  • browser notifications on key events              │
   └─────────────────────────────────────────────────────┘
```

Key invariants:

* **One workspace folder per task.** Each ticket id (`PROJ-12`) gets
  `~/.kato/workspaces/PROJ-12/` with fresh clones of every repo its
  `kato:repo:*` tags name. Two parallel tasks against the same repo are
  physically isolated checkouts — no shared branch state, no cross-task
  git races. Sized by `KATO_MAX_PARALLEL_TASKS`.
* **One subprocess per task id.** `ClaudeSessionManager` keyed on the
  ticket id; `--resume` keeps context across kato restarts.
* **The orchestrator and the webserver share the same managers.**
  `WorkspaceManager` (tab list source of truth) and
  `ClaudeSessionManager` (live subprocess + chat events) live in one
  Python process so the planning UI sees both in real time without IPC.
* **Workspace lifecycle = ticket state.** Workspaces are created when
  kato starts a task, persist across restarts via `.kato-meta.json`,
  and are deleted when the ticket leaves the Open + Review states
  (e.g. PR merged → Done).
* **Single-threaded gate, multi-threaded execute.** The scan loop
  pulls tasks from the ticket system one at a time; heavy execution
  (clone, run agent, test, publish) fans out across a thread pool.

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

1. `python -m kato_core_lib.main`, `kato up`, or the Docker entrypoint loads Hydra config and values from `.env`.
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

When the testing container is enabled and `OPENHANDS_SKIP_TESTING=false`, `kato compose-docker` starts Docker Compose with the `testing` profile so the extra `openhands-testing` service is available. When it is disabled, no dedicated testing server is started and the agent keeps testing on the main OpenHands instance. When `OPENHANDS_SKIP_TESTING=true`, the agent skips the validation step entirely and `kato compose-docker` stays on the normal profile even if the dedicated testing container is enabled.

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
