# Kato Rules

This repository uses Kato to implement YouTrack tasks and fix review comments.

# Git
do not do any git commit or push. let me inspect the changes

## Hard rules

- **Do not write code that already exists — search before writing.** Before adding any helper, hook, component, util, constant, or service method, `rg` for an existing one and reuse it. Duplicated logic and orphaned (uncalled) code are both defects.

### No redundancy — checks to run before you finish

- **Frontend duplication:** `cd webserver/ui && npm run dedup` (jscpd). Only the two annotated intentional clones are allowed; any new clone is a regression — dedupe it or annotate why it must stay. The gate fails (exit 1) above 0.3% duplication.
- **Backend dead imports:** `python -m pyflakes kato_core_lib webserver/kato_webserver`. The only expected hits are the intentional package re-exports in `comment_core_lib/__init__.py`, `data_layers/data/fields.py`, and `workspace_manager.py` (pyflakes ignores their `# noqa`), plus a couple of known unused locals. Any **new** unused import in a service/helper is dead code — remove it.
- **Orphan code:** a function, class, constant, or file with zero non-test callers is dead — delete it together with its dedicated test, unless it is an entry point (CLI subcommand, Flask route, `main`) or a documented intentional stub.
- **No shim / barrel files:** after extracting shared code, repoint importers to the canonical path and delete the re-export. A package `__init__.py` exposing its own package's API is the only allowed re-export.

### Reuse these before writing your own

- **Frontend (`webserver/ui/src/`):** `hooks/` (data-load + save state machines — `useSettingsResource`, `useRestartingSave`, `useSessionOption`, `usePolling`, `useBusyAction`; plus `useAutoSizeTextarea`, `useEscapeKey`, `useDismissOnOutsidePointerOrEscape`), `utils/` (`apiErrorMessage`, `cx`, `storage`, `pluralize`/`countNoun`, `clipboard`, `basenameOf`, `settingsSource`, `katoTags` for `kato:` tag prefixes/builders), `stores/toastStore.js` — use `toast.errorFromResult(result, {...})` / `toastResult(...)` for API-result toasts instead of hand-rolling the `{ kind: 'error', message: apiErrorMessage(...) }` envelope — and `components/settings/` panel scaffolding (`SettingsPanelBody`, `SettingsActions`, `RestartBanner`).
- **Backend:** `kato_core_lib/helpers/*_utils.py` — e.g. `kato_home_path` for any `~/.kato/<file>` path, `task_lookup_utils` (`find_task_by_id`, `task_id_matches`) for locating a task, `dotenv_utils` for `.env` parsing, `kato_tag_utils` for building/parsing any `kato:` task tag. Cross-`*_core_lib` duplication is the ONE intentional exception: the black-box libs must not import each other.

## Architecture

- Keep orchestration logic in services.
- Keep components free of heavy logic; move overlapping, reusable, or similar behavior into helpers or shared services instead of scattering it inside a component.
- Keep external API calls inside clients and data-access layers.
- When `AgentService` starts accumulating a second coherent workflow cluster, split it into a dedicated service and inject that service through the constructor instead of adding more private helper methods there. Preflight/startup logic such as model-access checks, blocking-comment retries, repository resolution, branch preparation, and push validation should live in a dedicated service rather than in `AgentService`.
- **`kato_core_lib/kato_core_lib.py` is composition-only.** Its job is to build the dependency graph (instantiate clients, services, validators) and inject them into `AgentService`. Do not add helper functions, prompt templates, config-key parsing, factory builders, or any other domain logic there. If a feature needs a small builder or parser, put it next to the feature: a classmethod on the owning service, a module-level helper in the service's file, or a `kato_core_lib/helpers/*_utils.py` module. The only content `kato_core_lib.py` should grow when you add a feature is more constructor calls and more keyword arguments.
- If a service starts collecting a grab-bag of pure helpers, formatting functions, or repeated logging wrappers, move them into `kato_core_lib/helpers/*_utils.py` or split them into a smaller service instead of keeping one oversized file.
- Do not add pass-through helper methods on `KatoCoreLib` when the service can be used directly.
- Prefer constants from `kato_core_lib/data_layers/data/fields.py` over free-text field names.
- **Never hand-write `kato:` task-tag strings** (`'kato:repo:...'`, `'kato:triage:...'`, `'kato:wait-...'`). Every kato tag is namespaced under `KATO_TAG_NAMESPACE` in `fields.py` and each segment (`repo`, `triage`, …) is defined once; build and parse tags through `kato_core_lib/helpers/kato_tag_utils.py` — `build_repository_tag` / `repository_id_from_tag` / `is_repository_tag` / `build_triage_tag` / `build_kato_tag`. The web client mirrors these in `webserver/ui/src/utils/katoTags.js` (`REPOSITORY_TAG_PREFIX`, `buildRepositoryTag`, …) — use it instead of typing the prefix into JSX, and keep the two files in sync.
- Reuse existing utilities before introducing duplicate helper logic.
- Do not create compatibility shim modules, barrel exports, or `__all__` re-export files; import from the real module directly.
- Put shared utility modules under `kato_core_lib/helpers/` instead of scattering them across service or root packages, and name them with the `_utils.py` suffix.
- Put validation rules under `kato_core_lib/validation/` instead of `data_layers/service/validation/`.
- Give each service class a short responsibility comment or docstring. If the description clearly contains more than one job, split that class into smaller collaborators instead of letting it grow.

## Required Behavior

- Read the full task context before changing code.
- Prefer small, direct changes over broad refactors.
- Keep the existing project structure and naming patterns.
- Do not add dependencies unless they are clearly required.
- Make configuration-driven behavior live in config or environment variables, not hardcoded values.
- Process assigned tasks sequentially, one after the other.
- Do not let work from one task leak into another task's branch, commit, pull request, or summary.
- Before starting a new task in a repository, verify the repository is clean and on the destination branch.
- When a task finishes, make sure all intended changes are committed and pushed before treating the task as ready for review.
- After publishing task work, return the repository to the destination branch and verify that branch switch succeeded before any later task can start.
- Do not mark a task as done, ready for review, or successfully published unless the branch is actually in a publishable state.

## Testing

- Write tests for new behavior when possible.
- Prefer focused unit tests close to the changed behavior.
- Run the relevant tests before opening a pull request.
- If tests fail, fix the code or the tests and rerun them until they pass.
- Add edge-case coverage for malformed payloads, retries, timeouts, and degraded downstream behavior when relevant.
- **Trust the gut.** If anything in the change feels even slightly under-tested — a new branch, a new exception, a new fallback, an interaction between two existing layers — write the test before moving on. Don't talk yourself out of it because "the happy path is covered" or "it would be over-testing." If you're considering whether a case needs coverage, the answer is yes; add the test.
- When fixing a real bug surfaced by the user, the regression test is a *requirement*, not optional. Each bug fix should ship with a test that fails on the old code and passes on the new.
- In tests, prefer existing entities and shared test helpers over ad hoc objects.
- Do not add test bootstrap shims or fake package injectors to work around missing required dependencies.
- Tests in this repository must run against the real installed packages and should fail fast if those packages are missing.
- Never reintroduce `tests/bootstrap.py`-style import patching, `sys.modules` injection, or fake package facades for required dependencies.
- If a test only passes with a shim, fix the environment or update the test to use the real package API instead of recreating the package in tests.
- Do not mock third-party libraries or `core-lib` behavior when the real installed package can be used directly in the test.
- Do not add mocks just to force a preferred exception type or API shape if the real library behavior is acceptable.
- Prefer lightweight real objects and real library calls over `Mock()` or `SimpleNamespace()` when the dependency is already cheap and deterministic.
- Add explicit tests for workflow boundaries when they change, especially:
  - sequential task processing
  - clean-worktree validation before starting the next task
  - commit and push requirements before publish
  - returning to the destination branch after publish success or failure
  - refusing to continue when branch or workspace validation fails

## Safety

- Do not remove user code unless it is part of the requested fix.
- Do not use destructive git commands.
- Preserve configuration compatibility unless the task explicitly changes it.
- Keep logging clear on any swallowed exception or degraded path.
- Do not add fallback code that hides missing required packages; required runtime dependencies should fail fast instead of being silently skipped or patched around.
- Do not add fallback config-shape handling for required settings; access the expected config directly and let invalid config fail fast.
- Do not add thin wrappers around upstream library methods unless they provide real domain behavior for this app.
- Do not translate upstream exceptions into local ones unless the boundary needs a deliberate application-level contract.
- Do not assume a task is safely published just because a branch exists or a summary was returned.
- Fail fast when commit, push, branch-reset, or publish preconditions are not met instead of silently continuing.

## Output

- Keep code readable.
- Reuse existing utilities instead of duplicating logic.
- Log failures clearly when a flow continues after an error.
- Before opening a pull request, make sure the implementation prompt instructions were followed, especially around tests.
- Do not present guesses, plans, or likely causes as confirmed facts.
- Do not rely on stack traces alone when debugging remote integrations. When possible, reproduce the failing remote/API call directly with the real endpoint and auth so the fix is based on the actual response, not only the traceback.
- In commentary and final responses, clearly separate:
  - what was directly observed in code, logs, or tests
  - what is an inference
  - what is the planned fix
- Do not say something is implemented, fixed, or validated until the code change and verification actually happened.

## Code Style

### Philosophy

This codebase favors:

- clarity over cleverness
- explicit structure over magic
- minimal output over verbosity
- small reusable helpers over duplication

The goal is to keep the code:

- easy to read
- easy to modify
- predictable during execution

### Naming Conventions

- Keep each dedicated module and its primary class aligned in name.
- Use `snake_case.py` for the file name and `CamelCase` for the class name.
- For example, a `StartupDependencyValidator` class should live in `startup_dependency_validator.py`, not a differently named utility file.
- Keep the branding image at the project root as `kato.png`.

### Class Declarations

- Always declare a base class explicitly. When a class doesn't extend anything else, write `class Foo(object):` instead of the bare `class Foo:`. This applies to data containers, namespaces, and ordinary services alike.
- The two are functionally identical in Python 3, but the explicit form makes it obvious at a glance that this is a new-style root class and matches the rest of the codebase. Files like `kato_core_lib/data_layers/data/fields.py` and the streaming-session classes follow this pattern; new code should match.

### Avoid Duplication

- If logic or values appear more than once, extract them.
- Extract repeated paths, hosts, filenames, and project names into constants.
- Extract repeated domain terms, status labels, and other shared string literals into constants when they are reused across multiple call sites or modules; do not create constants for one-off local prose.
- Extract repeated workflow steps such as git, scp, and path building into helpers.
- Prefer small reusable helpers over copy-pasted branches of similar code.
- When service-layer orchestration repeats the same control-flow, error handling, or logging pattern, extract a small private helper in that service before introducing a broader shared abstraction.
- When a service file starts carrying two different responsibilities, split the responsibilities into dedicated service/helper classes and keep the remaining class focused on one job.
- When deduplicating code, preserve the existing behavior, task-state transitions, log messages, and test-visible outputs unless the task explicitly requires changing them.

### Comments

- Comments must be short — **max 2 lines, simple, only when the *why* is non-obvious**. No paragraph explanations, no rationale dumps, no "why this approach vs that approach" essays. If you find yourself writing more than two lines, the explanation belongs in a PR description or a design doc, not the code.
- Do not reformat files. Pull-request diffs should show only the changes that were made — no whitespace churn, no import reorders unrelated to the change, no quote-style flips.

---

## React UI rules (`webserver/ui/`)

The planning UI under [`webserver/ui/`](webserver/ui/) is a small Vite + React app. The rules below apply only to JS/JSX files there; they don't apply to Python.

### No logic inside JSX

**The JSX returned from a component renders variables and components — nothing else.** All logic, including conditions, loops, and derived values, is computed before the `return` statement and assigned to a variable.

#### Anti-patterns to avoid

```jsx
// ❌ Inline logic / conditions / mapping inside JSX
{props.items && props.items.length > 0 && <List items={props.items} />}
{condition && renderMobileOnly()}
{otherCondition ? <ComponentA /> : <ComponentB />}
{options.map((item) => <Row {...item} />)}
```

```jsx
// ❌ Creating JSX unconditionally then hiding it
const content = <ExpensiveLabeledInputWrap>...</ExpensiveLabeledInputWrap>;
return <div>{condition ? content : null}</div>;
```

#### Good patterns

```jsx
// ✓ Extract condition + render call to a variable, BEFORE return
const mobileOnly = props.is_mobile_app_available && renderMobileOnly();
return (
  <div>
    {mobileOnly}
  </div>
);
```

```jsx
// ✓ Compute derived values before return
const isEmpty = items.length === 0;
const content = isEmpty ? <Empty /> : <List items={items} />;
return <div>{content}</div>;
```

```jsx
// ✓ For mapped lists, build the array before return
const rows = items.map((item) => <Row key={item.id} {...item} />);
return <ul>{rows}</ul>;
```

The `&&` and `?:` operators inside JSX *are* logic. Extract them.

### Component logic extraction

- **Components contain rendering only.** Any computation that isn't rendering — data transformations, filtering / grouping, set/map building, index translation, validation, domain rules, derived selectors — lives in a helper file, not in the component.
- **Rule of thumb:** if a block of code could be unit-tested without mounting React, it does not belong in the component. Move it to the nearest domain-appropriate helper (e.g. `<Feature>Helpers.js` next to the component file) and import it.
- **Where helpers live:** next to the feature, not in a top-level catch-all utilities module.
- **What stays in the component:** state (`useState`, `useReducer`), refs, effect wiring, call sites for helpers, prop plumbing, and JSX. Nothing else.
- **What specifically moves out:** `new Set()`/`new Map()` builders, `.forEach`/`.filter`/`.reduce` over props or derived data, boolean derivations beyond a single `&&`/ternary, loops that produce strings or objects for downstream use.

### Single Responsibility Principle for components

**One component does one thing.** A component renders one feature; it does not also contain three sibling features wedged into the same file.

Concrete checks before adding code to an existing component:

- **Does the new code share state with what's already there?** If no — it belongs in its own component, not bolted onto this one.
- **Could a future reader summarise the component in one sentence after the change?** If "renders the chat header AND formats four kinds of toasts AND owns the adopt-session modal AND…" — the component is doing too much; split.
- **Are the new computations testable on their own?** If yes, extract them to a sibling helper (see "Component logic extraction" above) so the component stays small and the logic gets covered by node-only tests.
- **Is the file growing past ~200 lines of JSX + handlers?** That is a signal — not a hard rule, but worth pausing — to check whether the component has accumulated multiple responsibilities.

When in doubt, prefer two small components over one branching component. The render path of each stays cheap; the diff of each future change stays small.

### Arrow functions

Always use curly braces `{}` and an explicit `return` in arrow functions. Never use the implicit-return (expression body) form.

```jsx
// ❌ Bad
const lerp = (a, b, t) => a + (b - a) * t;
const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

// ✓ Good
const lerp = (a, b, t) => {
  return a + (b - a) * t;
};
const clamp = (value, min, max) => {
  return Math.min(Math.max(value, min), max);
};
```

The exception is one-line callbacks passed inline (`items.map((x) => x.id)`); the rule targets *named* arrow functions you'd otherwise debug or step through.

### Styling

- **Styles are authored in SCSS, not plain CSS.** Edit [`webserver/ui/src/styles/constants.scss`](webserver/ui/src/styles/constants.scss) (design tokens) and `app.scss`, then run `npm run build:css` (in `webserver/ui`) to compile [`webserver/static/css/app.css`](webserver/static/css/app.css). **Never edit `app.css` directly — it is generated.** Don't introduce CSS-in-JS or styled-components.
- **No magic numbers.** Every color, space, radius, and font size comes from a `constants.scss` token: `$C-*` (colors), `$SPACE-*`, `$RADIUS-*`, `$TEXT-*`. Never hardcode a hex or px value in `app.scss`; reuse the nearest existing token, and add a new one only when none fits.
- **The color palette is deduplicated and stays that way:** at most 4 alpha variations per base color, and the base colors are kept perceptually distinct (≈ΔE ≥ 8). Before adding a color, find the nearest existing `$C-*` and reuse it; fold a near-duplicate into its neighbour rather than adding a token.
- Keep class names short and component-scoped (`status-bar`, `tab-forget-btn`, `files-tab-repo-header`). Don't introduce BEM (`__` / `--` separators).

### Tool preferences

- For file searches and grep work, prefer `rg` (ripgrep) over `find` + `grep`. Always scope: `rg "pattern" webserver/ui/src --type js -l`.
- For single-line edits / deletions, `sed -i` is fine. Don't read an entire file just to find one key — search first.
- Don't run `git diff`, `git status`, or any git command unless the task explicitly involves git.
