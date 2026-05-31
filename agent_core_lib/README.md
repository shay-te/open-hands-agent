# agent-core-lib

Shared agent-behavior layer plus the factory wrapper that picks the
configured agent backend (Claude / Codex / OpenHands) and exposes it
through the shared [`AgentProvider`](../agent_provider_contracts/)
interface. Same shape as [`task_core_lib`](../task_core_lib/) and
[`repository_core_lib`](../repository_core_lib/), but focused on what
an agent should see before a backend sends work to a model.

## What lives here

```
agent_core_lib/agent_core_lib/
├── agent_core_lib.py                ← AgentCoreLib composition root
├── platform.py                       ← AgentPlatform enum
├── client/
│   └── agent_client_factory.py       ← AgentClientFactory + resolve_platform()
└── helpers/
    ├── agent_prompt_utils.py         ← prompt scope, safety, review context
    ├── agents_instruction_utils.py   ← AGENTS.md discovery/rendering
    ├── architecture_doc_utils.py     ← architecture doc loading
    ├── lessons_doc_utils.py          ← lessons doc loading
    ├── resume_prompt_utils.py        ← generic resume prompt snapshots
    ├── credential_scan.py            ← output-side credential/phishing scan
    ├── result_utils.py               ← normalized agent result payloads
    └── session_id_utils.py           ← generic agent session id helpers
```

## Public surface

```python
from agent_core_lib.agent_core_lib import AgentCoreLib, AgentPlatform
from agent_core_lib.agent_core_lib.client.agent_client_factory import resolve_platform

platform = resolve_platform(cfg.kato.agent_backend)  # 'claude' / 'openhands' / aliases
agent = AgentCoreLib(
    platform,
    cfg.kato,
    max_retries=3,
    docker_mode_on=True,
    read_only_tools_on=False,
).agent
# agent is typed as AgentProvider — call by interface, never branch on backend.
agent.implement_task(task, prepared_task=ctx)
```

## Responsibilities

- **Prompt Preparation**: Build the reusable prompt scaffolding an
  agent needs before a backend sends work to a model.

- **Safety Guardrails**: Add generic safety text for untrusted task
  descriptions, comments, logs, attachments, and quoted content.

- **Workspace Scope**: Render strict "only read or edit these paths"
  boundaries for per-task workspaces and repository clones.

- **Repository Scope**: Explain which repositories and branches are in
  scope without teaching product-specific task workflow.

- **Caller Guidance Hook**: Accept optional caller-provided guidance,
  such as product-specific refusal instructions, while keeping this
  lib product-agnostic.

- **AGENTS.md Instructions**: Discover and render checked-in
  `AGENTS.md` files so backend agents follow repository-local rules.

- **Architecture Docs**: Load architecture documentation that callers
  can append to an agent's system prompt.

- **Lessons Memory**: Load compacted lessons text that callers can
  include in future agent prompts.

- **Review Context**: Add review-comment context such as file path,
  line number, commit id, nearby code, and prior thread text.

- **Conversation Continuity**: Add guidance that helps agents trust
  existing conversation history instead of repeating expensive reads or
  git inspection.

- **Result Normalization**: Normalize backend result payloads into the
  shared agent-result shape expected by orchestration code.

- **Session IDs**: Normalize and preserve generic agent session ids
  across backend boundaries.

- **Resume Snapshots**: Render generic markdown snapshots that let
  another agent continue from recent conversation state.

- **Output Safety Scan**: Detect credential-looking or phishing-looking
  text in model output and log redacted audit warnings.

- **Backend Factory**: Resolve the configured backend name, construct
  the selected `AgentProvider`, and pass runtime knobs through to that
  backend.

## Non-Responsibilities

- **Model Providers**: Does not call Bedrock, OpenRouter, OpenAI,
  Anthropic, or other model APIs directly. Provider transport belongs
  in provider/LLM-specific libraries.

- **Product Workflow**: Does not know about Kato tasks, ticket states,
  review publishing, PR creation, or UI workflows.

- **Issue Platforms**: Does not contain YouTrack, Jira, GitHub,
  GitLab, or Bitbucket workflow behavior.

- **Backend Runtime**: Does not own Claude/Codex subprocess logic or
  OpenHands HTTP/container plumbing. Each backend core-lib owns its
  runtime details.

## Why a factory pattern at all

Kato used to have `if is_claude_backend(): build_claude_client(...) else: build_openhands_client(...)` branches scattered through its composition root. Each new backend added one more branch and one more `KatoClient | ClaudeCliClient` union type. The factory collapses that to one place; kato sees only `AgentProvider` past the boot wire-up.

Adding a new backend (e.g., future `codex_core_lib`) means:
1. Add `CODEX = 'codex'` to `AgentPlatform`.
2. Wire its alias(es) in `_PLATFORM_ALIASES`.
3. Add a `_build_codex` branch in `AgentClientFactory.build`.
Kato itself doesn't change.

## Tests

```
agent_core_lib/agent_core_lib/tests/
```

Pin: alias resolution (every operator-typed string maps to the
right enum), unknown-backend error message (must name the supported
options so the operator knows what to fix), factory dispatch
(routes the right enum to the right builder).

Construction of the actual backend objects is tested where they
live — `claude_core_lib` tests cover `ClaudeCliClient` construction,
`openhands_core_lib` tests cover `KatoClient` construction.
