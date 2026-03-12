# OpenHands Rules

This repository uses OpenHands to implement YouTrack tasks and fix review comments.

## Architecture

- Keep orchestration logic in services.
- Keep external API calls inside clients and data-access layers.
- Do not add pass-through helper methods on `OpenHandsAgentCoreLib` when the service can be used directly.
- Prefer constants from `fields.py` over free-text field names.
- Reuse existing utilities before introducing duplicate helper logic.

## Required Behavior

- Read the full task context before changing code.
- Prefer small, direct changes over broad refactors.
- Keep the existing project structure and naming patterns.
- Do not add dependencies unless they are clearly required.
- Make configuration-driven behavior live in config or environment variables, not hardcoded values.

## Testing

- Write tests for new behavior when possible.
- Prefer focused unit tests close to the changed behavior.
- Run the relevant tests before opening a pull request.
- If tests fail, fix the code or the tests and rerun them until they pass.
- Add edge-case coverage for malformed payloads, retries, timeouts, and degraded downstream behavior when relevant.
- In tests, prefer existing entities and shared test helpers over ad hoc objects.

## Safety

- Do not remove user code unless it is part of the requested fix.
- Do not use destructive git commands.
- Preserve configuration compatibility unless the task explicitly changes it.
- Keep logging clear on any swallowed exception or degraded path.

## Output

- Keep code readable.
- Reuse existing utilities instead of duplicating logic.
- Log failures clearly when a flow continues after an error.
- Before opening a pull request, make sure the implementation prompt instructions were followed, especially around tests.
