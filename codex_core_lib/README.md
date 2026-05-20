# codex-core-lib

OpenAI Codex CLI agent backend. Implements
[`agent_provider_contracts.AgentProvider`](../agent_provider_contracts/)
so kato (and any other orchestrator) can call into Codex through
the same contract every other backend
([`claude_core_lib`](../claude_core_lib/),
[`openhands_core_lib`](../openhands_core_lib/)) satisfies.

## What lives here

```
codex_core_lib/codex_core_lib/
├── cli_client.py               ← CodexCliClient (implements AgentProvider)
└── helpers/
    └── one_shot_utils.py       ← codex_one_shot / make_codex_one_shot
```

That's it — everything else (prompt builders, architecture-doc
reader, lessons reader, AGENTS.md reader, result-shape helpers,
text utilities, ImplementationFields) lives in
[`agent_core_lib`](../agent_core_lib/) and is shared with
[`claude_core_lib`](../claude_core_lib/) so the two CLI backends
present an identical helper surface.

## Public surface

```python
from codex_core_lib.codex_core_lib import CodexCliClient
```

`CodexCliClient` is what `agent_core_lib`'s factory returns when
`KATO_AGENT_BACKEND=codex` (or any alias: `codex-cli`, `codex_cli`,
`openai-codex`, `openai_codex`) is configured.

## Parity with `ClaudeCliClient`

Every public method (`validate_connection`, `validate_model_access`,
`implement_task`, `test_task`, `investigate`, `fix_review_comment`,
`fix_review_comments`, `delete_conversation`,
`stop_all_conversations`) has the same name, same signature, and
returns the same shape as its `ClaudeCliClient` counterpart. The
private differences live in `_build_command` (Codex CLI flags
differ from Claude Code flags) and `_parse_completed_process`
(JSON envelope shape).

## Differences from `claude_core_lib`

| Aspect | Claude | Codex |
|---|---|---|
| Binary default | `claude` | `codex` |
| Non-interactive entry | `claude -p` | `codex exec` |
| JSON output flag | `--output-format json` | `--json` |
| Permission flag | `--permission-mode <mode>` | `--ask-for-approval <mode>` |
| Reasoning flag | `--effort` | `--reasoning-effort` |
| Allow / deny flags | `--allowedTools` / `--disallowedTools` | `--allow-tools` / `--deny-tools` |
| Workspace flag | `--add-dir` | `--workspace` |
| System-prompt append flag | `--append-system-prompt` | `--system-prompt-append` |
| Non-interactive env hint | `CLAUDE_CODE_NONINTERACTIVE=1` | `CODEX_NONINTERACTIVE=1` |
| Session resume flag | `--resume <id>` | `--resume <id>` (shared) |

Streaming chat sessions and `--resume`-driven multi-round review
flows aren't wired yet — the current client is one-shot. When
Codex CLI's streaming support is added, the streaming machinery
will follow the same file layout `claude_core_lib` uses (a
`session/` folder with the same module names).

## Configuration

```env
KATO_AGENT_BACKEND=codex
KATO_CODEX_BINARY=codex
KATO_CODEX_MODEL=
KATO_CODEX_MAX_TURNS=
KATO_CODEX_EFFORT=
KATO_CODEX_ALLOWED_TOOLS=
KATO_CODEX_DISALLOWED_TOOLS=
KATO_CODEX_BYPASS_PERMISSIONS=false
KATO_CODEX_TIMEOUT_SECONDS=1800
KATO_CODEX_MODEL_SMOKE_TEST_ENABLED=false
```

Install Codex CLI:

```bash
npm install -g @openai/codex
```

Authenticate once on the host (e.g. `codex login`) before pointing
kato at it — kato runs the CLI non-interactively and inherits the
credentials the CLI already has.

## Tests

```bash
python -m unittest discover -s codex_core_lib -p "test_*.py" -t .
```
