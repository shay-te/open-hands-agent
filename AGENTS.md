# OpenHands Rules

This repository uses OpenHands to implement YouTrack tasks and fix review comments.

## Required Behavior

- Read the full task context before changing code.
- Prefer small, direct changes over broad refactors.
- Keep the existing project structure and naming patterns.
- Do not add dependencies unless they are clearly required.

## Testing

- Write tests for new behavior when possible.
- Prefer focused unit tests close to the changed behavior.
- Run the relevant tests before opening a pull request.
- If tests fail, fix the code or the tests and rerun them until they pass.

## Safety

- Do not remove user code unless it is part of the requested fix.
- Do not use destructive git commands.
- Preserve configuration compatibility unless the task explicitly changes it.

## Output

- Keep code readable.
- Reuse existing utilities instead of duplicating logic.
- Log failures clearly when a flow continues after an error.
