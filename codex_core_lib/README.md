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

Verified against **codex-cli 0.132.0** (the OpenAI `@openai/codex`
package). If you upgrade codex, re-run `codex exec --help` and
adjust this table + `cli_client.py::_build_command` if anything
moved.

| Aspect | Claude | Codex |
|---|---|---|
| Binary default | `claude` | `codex` |
| Non-interactive entry | `claude -p` | `codex exec` |
| JSON / event stream | `--output-format json` (single JSON object) | `--json` (JSONL event stream, one event per line) |
| Final-message capture | parsed from JSON `result` field | `-o, --output-last-message <FILE>` writes the agent's final reply to a file |
| Permission flag | `--permission-mode acceptEdits\|bypassPermissions` | **none on `codex exec`** — `--ask-for-approval` is a top-level interactive-mode flag, not on the non-interactive subcommand. Approval policy comes from `~/.codex/config.toml` (`approval_policy`) or a `-c approval_policy=<value>` override. |
| Bypass mode | `--permission-mode bypassPermissions` | `--dangerously-bypass-approvals-and-sandbox` (single flag, no value; conflicts with `--sandbox`) |
| Sandbox containment | (no built-in flag; relies on docker wrapper) | `--sandbox read-only\|workspace-write\|danger-full-access` (built into the CLI) |
| Tool allow/deny lists | `--allowedTools` / `--disallowedTools` | **none** — sandbox mode + execpolicy `.rules` files take their place |
| Reasoning depth | `--effort low\|medium\|high\|xhigh\|max` | **no flag** — set via `~/.codex/config.toml` key `model_reasoning_effort` or `-c model_reasoning_effort=high` config override |
| Max turns | `--max-turns N` | **none** — Codex 0.132 has no per-invocation turn cap |
| Working directory | subprocess `cwd=` | `-C, --cd <DIR>` (explicit flag) |
| Additional writable dirs | `--add-dir <DIR>` | `--add-dir <DIR>` (same name) |
| System-prompt append flag | `--append-system-prompt <text>` | **none** — kato prepends the architecture-doc + lessons text to the user prompt instead |
| Non-interactive env hint | `CLAUDE_CODE_NONINTERACTIVE=1` | **none** — `--json` on `codex exec` already disables TTY behaviour |
| Session resume | `--resume <id>` flag | `codex exec resume <id>` sub-subcommand (NOT a flag). Resume accepts a **restricted flag subset** — `--sandbox`, `-C`, `--add-dir` are rejected (resumed sessions inherit those from the original spawn); `--json`, `-o`, `-m`, `--skip-git-repo-check`, `--dangerously-bypass-*`, `-c` are accepted. |
| Workspace-outside-git escape hatch | n/a | `--skip-git-repo-check` (kato always sets this since workspaces aren't always git roots) |

Streaming chat sessions and the planning-UI tab integration aren't
wired yet — the current client is one-shot. When Codex's
streaming surface is added, the streaming machinery will follow
the same file layout `claude_core_lib` uses (a `session/` folder
with the same module names).

### Knobs accepted but ignored

These constructor params exist on `CodexCliClient` so the factory
can call it with the same kwargs it calls `ClaudeCliClient` with,
but they have **no effect** because Codex 0.132 has no equivalent:

- `max_turns`
- `effort`
- `allowed_tools`
- `disallowed_tools`
- `read_only_tools_on`

Kato logs one info line at construction listing these so an
operator who set them on the wrong backend isn't left guessing.

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
