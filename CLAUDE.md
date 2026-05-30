# Kato — Claude Briefing

Kato is an autonomous coding agent. It polls YouTrack/Jira/Bitbucket for assigned tasks, clones repos into isolated per-task workspaces, runs an AI agent (OpenHands or Claude) to implement the fix, then pushes a branch and opens a PR. Also handles Bitbucket PR review comments (fix or answer).

## Run & Test

```bash
pip install -e .     # puts the `kato` CLI on PATH (replaces the Makefile)
kato up              # start kato locally (.env + run main)
kato test            # run the unittest suite
```

`kato` is the single operator entry point — `kato up | bootstrap | configure | doctor | test | build-agent-server | sandbox <build|login|verify> | compose-docker`. There is no Makefile. The suite can also be run directly: `python -m unittest discover -s tests -p "test_*.py"`.

94 pre-existing errors in `openhands_core_lib` — ignore. Zero failures expected.

**Keep the code redundancy-free** (full rules in AGENTS.md → "No redundancy"). Before finishing work:
- `cd webserver/ui && npm run dedup` — frontend duplicate-code gate (jscpd; fails above 0.3%; only the 2 intentional clones are allowed).
- `python -m pyflakes kato_core_lib webserver/kato_webserver` — backend dead-import gate (expected hits are the package re-exports in `comment_core_lib/__init__.py`, `data_layers/data/fields.py`, `workspace_manager.py`, plus a couple known unused locals; any NEW finding is dead code to remove).
- Reuse the shared hooks/utils/helpers under `webserver/ui/src/{hooks,utils,stores}` and `kato_core_lib/helpers/*_utils.py` instead of re-implementing; delete orphan (uncalled) code together with its test.

**Never run `npm run build`** — the React bundle is pre-compiled. Running it takes 30+ seconds, requires Node.js to be installed, and is not needed for backend changes or Python tests. To rebuild the frontend (only when changing files under `webserver/ui/src/`):

```bash
cd webserver/ui
npm install
npm run build
```

---

## Core-Lib Architecture

Monorepo of **closed black-box libs**. Each lib is self-contained — no peer-to-peer imports. `kato_core_lib` is the top-level orchestrator; everything else is independent.

```
kato_core_lib                  ← orchestrator (may import any lib below)
├── git_core_lib               ← GitClientMixin, git subprocess engine, repo discovery utils
├── repository_core_lib        ← provider utils (URL parsing, token messages)
├── claude_core_lib            ← Claude CLI client, one-shot utils, streaming sessions
├── task_core_lib              ← task data types and platform config
├── bitbucket_core_lib
├── github_core_lib
├── gitlab_core_lib
├── youtrack_core_lib          ← YouTrack API client (fully black-box, see standard below)
├── jira_core_lib
├── workspace_core_lib         ← workspace folder management
├── provider_client_base       ← ReviewComment and shared provider types
├── agent_core_lib
└── openhands_core_lib
```

**Rule:** code that only uses types/utils from one lib belongs IN that lib. Glue between libs belongs in `kato_core_lib`.

### Core-Lib Quality Standard

Every core-lib must meet all of these (use `youtrack_core_lib` as the reference example):

1. **100% test coverage** — every service function, every permutation of inputs
2. **Flow tests A-Z** — end-to-end flow tests inside the lib's own `tests/` folder (`test_flow.py`)
3. **No other core-lib imports** — zero peer dependencies. Only stdlib + third-party packages
4. **No kato references** — no `kato_core_lib` imports, no kato-specific field names. If something kato-specific is needed, pass it as a constructor parameter or config value
5. **Tests live inside the lib** — at `<lib>/<lib>/tests/`, not in the top-level `tests/` folder
6. **Check for leaked tests** — after building a lib, grep `kato_core_lib/` and `tests/` for any tests that belong inside the lib instead

```bash
# Check a lib is clean
grep -rn "kato_core_lib" <lib_name>/<lib_name>/ --include="*.py"   # must be empty
grep -rn "<lib_name>" kato_core_lib/ --include="*.py"              # only import lines, no logic leaks
grep -rn "<lib_name>" tests/ --include="*.py"                      # should be empty if tests are inside the lib
```

### What Was Migrated Out of kato_core_lib

Code moved in previous sessions — do not move it back:

| Was in kato_core_lib | Now lives in |
|---|---|
| `helpers/claude_one_shot_utils.py` (re-export) | `claude_core_lib/claude_core_lib/helpers/one_shot_utils.py` |
| `helpers/git_clean_utils.py` (re-export) | `git_core_lib/git_core_lib/helpers/git_clean_utils.py` |
| `helpers/repository_discovery_utils.py` (re-export) | `git_core_lib/git_core_lib/helpers/repository_discovery_utils.py` |
| `data/review_comment.py` (re-export) | `provider_client_base/provider_client_base/data/review_comment.py` (direct import) |
| All git subprocess methods on RepositoryService | `git_core_lib/git_core_lib/client/git_client.py` → `GitClientMixin` |
| `_fallback_web_base_url`, `_provider_from_url_string`, `_default_provider_base_url`, `_missing_pull_request_token_message` | `repository_core_lib/repository_core_lib/helpers/provider_utils.py` |

**RepositoryService inheritance:**
```python
class RepositoryService(GitClientMixin, RepositoryInventoryService): ...
```
`GitClientMixin` owns all `git` subprocess calls. `RepositoryInventoryService` owns repo config/discovery.

---

## Key Files

| File | What it does |
|------|-------------|
| `kato_core_lib/main.py` | Entry point, scan loop (30s interval, 5s startup delay) |
| `kato_core_lib/jobs/process_assigned_tasks.py` | Each scan cycle: dispatch tasks + review comments |
| `kato_core_lib/data_layers/service/agent_service.py` | Top-level service object — owns all sub-services |
| `kato_core_lib/data_layers/service/task_preflight_service.py` | Pre-flight: resolve repos, clone workspaces, prep branches |
| `kato_core_lib/data_layers/service/workspace_provisioning_service.py` | Parallel git clone per task into `~/.kato/workspaces/<task>/<repo>/` |
| `kato_core_lib/data_layers/service/repository_service.py` | Repo operations (inherits GitClientMixin + RepositoryInventoryService) |
| `kato_core_lib/data_layers/service/repository_inventory_service.py` | Repo config loading + auto-discovery of `.git` folders |
| `kato_core_lib/data_layers/service/task_publisher.py` | Push branch, open PR, move task to "In Review" |
| `kato_core_lib/data_layers/service/review_comment_service.py` | Fix or answer PR review comments |
| `kato_core_lib/validation/startup_dependency_validator.py` | All connections validated in parallel at boot |
| `kato_core_lib/validation/repository_connections.py` | Per-repo git connectivity check |
| `kato_core_lib/helpers/review_comment_utils.py` | `is_question_comment()` heuristic + reply body builders |
| `git_core_lib/git_core_lib/client/git_client.py` | `GitClientMixin` — every `git` subprocess call |
| `git_core_lib/git_core_lib/helpers/repository_discovery_utils.py` | Disk walk to find `.git` folders |
| `repository_core_lib/repository_core_lib/helpers/provider_utils.py` | Provider URL/token utilities |

---

## Flows

### Startup
```
main() → KatoInstance.init(cfg)
       → validate_connections()          ← repos + task + impl + testing ALL validated in parallel
       → warm_up_repository_inventory()  ← background thread starts disk walk immediately
       → _run_task_scan_loop()           ← every 30s
```

### Task pickup
```
scan → get_assigned_tasks() [API]
     → process_assigned_task(task)
     → prepare_task_execution_context()
         1. resolve_task_repositories()  ← uses cached inventory (warm-up already ran)
         2. provision_workspace_clones() ← parallel git clone (up to 4 at once)
         3. git fetch + checkout branch
     → agent session (OpenHands or Claude streaming)
     → publish_task_execution()
         → create_pull_request() per repo
         → if all repos unchanged → status NO_CHANGES, task stays in current state (NOT moved to "In Review")
         → if PRs created → move to "In Review", post summary comment
```

### PR review comment
```
scan → get_new_pull_request_comments() on PRs in "In Review"
     → is_question_only_batch()?
         YES → agent answers → post reply with "NO CODE CHANGED" disclaimer → leave thread OPEN
         NO  → agent fixes → _review_fix_produced_changes()?
                  NO  → post "no changes" reply → raise (thread stays open)
                  YES → push → resolve thread
```

**`is_question_comment` heuristic:** requires `?` ending + question start word + no fix keywords + ≤400 chars. Conservative — defaults to fix-mode on ambiguity.

### False-success guards (important — previously bugs)
- Task: all repos unchanged → `NO_CHANGES` status, task NOT moved to "In Review"
- Review answer: thread never auto-resolved, reply always prefixed with visible "no code changed" disclaimer

---

## Config

```yaml
kato:
  task_scan:
    startup_delay_seconds: 5    # default
    scan_interval_seconds: 30   # default
```

Auto-discovery: if `REPOSITORY_ROOT_PATH` is set (no explicit `repositories:` list), Kato walks the tree for `.git` folders. Result cached after first run. Background warm-up runs this at boot.

---

## Testing Patterns

- All `unittest.mock.Mock` + `SimpleNamespace` — no network, no DB
- Each core-lib has its own `tests/` folder inside it
- Top-level `tests/` is for kato integration tests only
- Key test files: `test_task_publisher.py`, `test_startup_validator.py`, `test_repository_connections_validator.py`, `test_review_comment_question_mode.py`
