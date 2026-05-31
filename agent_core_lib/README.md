# agent-core-lib

A reusable **agent-behavior** layer plus a small **agent-backend factory**
for building coding/automation agents on top of CLI agent runtimes
(Claude Code, Codex, OpenHands).

It owns the *generic* work that every agent backend needs **before** a
prompt is sent to a model — prompt scaffolding, safety guardrails,
workspace/scope boundaries, AGENTS.md and architecture/lessons
injection, session-id normalization, and normalized result payloads —
and a factory that hands you the configured backend behind one
`AgentProvider` interface.

It is **product-agnostic**: it knows about agent *backends* (Claude /
Codex / OpenHands) because it builds providers, but it knows nothing
about any particular product's workflow, ticketing system, UI, or
environment naming. Anything product-specific is passed in by the host
application.

## What problem it solves

Each agent backend re-implements the same "prep" work: build a prompt
that tells the agent which files it may touch, inject the project's
checked-in conventions, normalize the model's session id, scan output
for leaked credentials, and shape the result into a common envelope.
Done per-backend, this drifts and duplicates. `agent_core_lib`
centralizes it so every backend behaves identically, and a single
factory selects the backend from config.

## Responsibilities

- **Prompt preparation** — reusable scaffolding an agent sees before a
  backend sends work to a model.
- **Safety guardrails** — generic instructions for handling untrusted
  task text, comments, logs, and attachments.
- **Workspace & repository scope** — strict "only read/edit these
  paths" boundaries, and which repos/branches are in scope, without
  encoding any product's task workflow.
- **Convention injection** — discover/render `AGENTS.md`, architecture
  docs, and a learned-lessons file into the prompt.
- **Review context** — file/line/commit localization and prior-thread
  context for review-comment fix prompts.
- **Conversation continuity** — guidance that helps an agent trust
  existing history instead of repeating expensive reads.
- **Session-id + result normalization** — one canonical session-id
  representation and a normalized result envelope across backends.
- **Output-side safety scan** — detective credential/phishing scan over
  the agent's final response (logs redacted previews only).
- **Resume snapshots** — render a generic markdown snapshot so another
  agent can continue from recent conversation state.
- **Backend factory** — pick the configured backend and expose it
  through the shared `AgentProvider` interface.
- **Caller guidance hook** — accept optional caller-provided guidance
  (e.g. product-specific refusal text) while staying product-agnostic.

## Non-responsibilities (explicitly NOT owned here)

- **Provider / model transport.** How a backend actually spawns a
  process, talks to a model API, streams tokens, applies permission
  modes, or wraps a sandbox lives in that backend's own library
  (`claude_core_lib`, `codex_core_lib`, `openhands_core_lib`) — not
  here. This library calls no model API directly.
- **Product workflow.** Ticketing, repo publishing, PR/review
  orchestration, schedulers, and any product UI belong to the host
  application.
- **Product-specific prompt text.** Guidance tied to a product (e.g.
  "to widen scope, do X in your tool") is **passed in** by the host
  (see `extra_refusal_guidance` below); this library never hardcodes
  it.

## Installation

```bash
pip install agent-core-lib   # placeholder — package name TBD on publish
```

Backend libraries are **optional** and imported lazily, so you only
need the dependency for the backend you actually use.

## Quick start

```python
from agent_core_lib.agent_core_lib import AgentCoreLib, AgentPlatform
from agent_core_lib.agent_core_lib.client.agent_client_factory import resolve_platform

# `config` is YOUR application's config object. The factory reads, by
# attribute: `agent_backend` plus a per-backend block (`config.claude`,
# `config.codex`, `config.openhands`) and `repository_root_path`.
config = load_your_config()

platform = resolve_platform(config.agent_backend)   # 'claude' / 'codex' / 'openhands' (+ aliases)
agent = AgentCoreLib(
    platform,
    config,
    max_retries=3,
    docker_mode_on=False,
    read_only_tools_on=False,
    # Optional: product-specific refusal guidance appended to the generic
    # workspace boundary. Supplied by YOU, never hardcoded in this library.
    workspace_refusal_guidance='',
).agent

# `agent` is typed as AgentProvider — call by interface, never branch on backend.
agent.implement_task(task, prepared_task=ctx)
```

## Supported backends

| Platform | `AgentPlatform` | Runtime lives in |
|---|---|---|
| Claude Code | `AgentPlatform.CLAUDE` | `claude_core_lib` |
| Codex | `AgentPlatform.CODEX` | `codex_core_lib` |
| OpenHands | `AgentPlatform.OPENHANDS` | `openhands_core_lib` |

The factory imports a backend lazily, inside its build method — a
Claude-only install never imports the Codex/OpenHands trees, and vice
versa. Adding a backend means adding an `AgentPlatform` member, its
config-key wiring, and a lazy build method.

## Prompt-helper example

The helpers are pure functions you can use without the factory:

```python
from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    workspace_scope_block,
    security_guardrails_text,
)

# Generic, product-agnostic strict boundary. Names only the allowed paths
# + generic env vars (AGENT_WORKSPACES_ROOT / AGENT_REPOSITORY_ROOT_PATH).
block = workspace_scope_block(['/abs/path/to/task/workspace'])

# A host that knows how to widen scope in its own product can append its
# own actionable refusal guidance — appended verbatim:
block = workspace_scope_block(
    ['/abs/path/to/task/workspace'],
    extra_refusal_guidance='To widen scope: <your product-specific steps>',
)

guardrails = security_guardrails_text()   # generic untrusted-content rules
```

## Backend-factory example

```python
from agent_core_lib.agent_core_lib.client.agent_client_factory import (
    AgentClientFactory,
)
from agent_core_lib.agent_core_lib.platform import AgentPlatform

factory = AgentClientFactory(
    max_retries=3,
    docker_mode_on=False,
    read_only_tools_on=False,
    workspace_refusal_guidance='',   # host-supplied, optional
)
provider = factory.build(AgentPlatform.CLAUDE, config)
result = provider.implement_task(task, prepared_task=ctx)
```

## Architecture boundaries

- `agent_core_lib` is **generic agent behavior**. It must not contain
  product/workflow/UI text or import a host application's package.
- Product-specific text (e.g. refusal guidance) is **injected by the
  host** as a parameter (`extra_refusal_guidance` /
  `workspace_refusal_guidance`).
- Backend **transport** (Claude/Codex/OpenHands process and API
  details) stays in each backend library; `agent_core_lib` only selects
  and composes them behind `AgentProvider`.
- Backend imports are **lazy** (function-local in the factory) so the
  base depends on no backend at import time.

### Configuration

Genericized env vars (read with sensible fallbacks where applicable):

| Env var | Purpose |
|---|---|
| `AGENT_IGNORED_REPOSITORY_FOLDERS` | Comma-separated repo folder names the agent must not touch. |
| `AGENT_WORKSPACES_ROOT` | Named in the scope block as the per-task workspaces root (informational text only). |
| `AGENT_REPOSITORY_ROOT_PATH` | Named in the scope block as the shared source-clones root (informational text only). |

For backward compatibility, `ignored_repository_folder_names()` falls
back to a legacy `KATO_IGNORED_REPOSITORY_FOLDERS` value when the generic
var is unset — compatibility only; prefer the generic name.

## Development / testing

```bash
# Run this library's own test suite (no network, no DB — all fakes):
python -m unittest discover -s agent_core_lib/agent_core_lib/tests -p "test_*.py"
```

Tests are self-contained (fake API keys / `localhost` URLs / fake model
names) and live inside the library. The library imports no host
application package; backend libraries are imported lazily only when a
provider for that backend is built.
