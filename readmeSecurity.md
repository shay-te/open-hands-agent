# Security Model — kato

What kato does to contain a misbehaving agent, what it doesn't, and what's on you. For vulnerability disclosure + the longer threat model, see [SECURITY.md](SECURITY.md). For the bypass-permissions defense layers, see [BYPASS_PROTECTIONS.md](BYPASS_PROTECTIONS.md).

## Three security gates run before any agent touches a repository

- **Repository denylist** (`KATO_REPOSITORY_DENYLIST`) — repos
  matched against this list are dropped from kato's inventory at
  load time. There is no override; if a repo id is on the denylist,
  kato will not see it.
- **Pre-execution security scanner** — every per-task workspace
  clone is scanned by `detect-secrets`, `bandit`, `safety`, `npm
  audit`, and a `.env`-file checker before the agent spawns. Real
  secrets / CVEs / dangerous patterns block the task with a ticket
  comment.
- **Restricted Execution Protocol (REP)** — kato refuses to run
  an agent against any repository the operator hasn't explicitly
  approved. Run `./kato approve-repo` to open the picker — it
  shows every repo kato can find with `[x]` next to the ones
  already approved; toggle indices, press Enter to apply. REP is
  always on. There is no off switch. New repos start in
  `restricted` mode; the operator elevates to `trusted` after
  reviewing the first agent run.

Combined, these catch the most common breach patterns in
small-team codebases — committed `.env` files, vulnerable deps,
and misrouted task tags.

## How much kato sandboxes the agent today

Kato hands large amounts of trust to the underlying agent (Claude / OpenHands): the agent reads the task description, decides which files to edit, and writes the changes. What kato actually does to contain a misbehaving agent today:

- **Prompt-level guardrails** baked into every kato prompt ([cli_client.py](kato_core_lib/client/claude/cli_client.py)) ask the agent not to touch credentials, escape the repository, or run git commands. These are advisory — a sufficiently determined or compromised model can ignore them.
- **Per-tool permission prompts via the planning UI** when `KATO_CLAUDE_BYPASS_PERMISSIONS=false` (the default). Each Bash / write-style tool call fires a modal that you Approve / Deny by hand, and the decision is sent back to Claude before it can act. This is the real interactive safety layer; it only works when you're watching.
- **Per-task workspace isolation on the filesystem.** Each task gets a fresh clone under `~/.kato/workspaces/<task-id>/`. Two parallel tasks don't share branch state. This is isolation between *tasks*, not between *the agent and your machine*.

What kato does **not** do today:

- Network isolation for the agent (it has the same network access as the host kato process).
- Filesystem sandboxing (the agent can read anything the kato process can).
- Per-task containerization for the agent.

`KATO_CLAUDE_BYPASS_PERMISSIONS=true` removes the planning-UI prompt layer in exchange for unattended speed. To make this state impossible to enable silently or by accident, kato applies the following defense-in-depth layers:

- **Refused under root.** Kato will not start when `KATO_CLAUDE_BYPASS_PERMISSIONS=true` and the process runs as root. There is no exception and no override.
- **Refused under CI / Docker / cron / systemd.** When stdin is not a TTY, kato refuses to start with bypass on — there is no flag-only escape hatch. Acknowledgement must come from a real terminal. Either run kato interactively to confirm, or unset the flag.
- **Double-prompt on every interactive boot.** When stdin is a TTY, kato asks the operator twice with `prompt_yes_no` ("are you sure?" then "final confirmation, this disables every per-tool prompt for the entire session?"). Either no aborts startup. A fat-fingered Enter cannot slip through.
- **Unmissable stderr banner** at every boot, written before logger configuration so log level cannot suppress it.
- **Persistent red banner across the top of the planning UI** — every operator looking at the browser sees the bypass state.
- **Configurator requires typing `I ACCEPT`** before writing the flag (`python -m kato_core_lib.configure_project`).
- **Per-spawn `WARNING` log** on every Claude turn naming the loss of per-tool prompts.

Only enable bypass when you've already locked the agent down at a different layer (devcontainer, dedicated VM, scoped credentials, egress firewall — see SECURITY.md).

The actual safety net is the same one you use for human contributors: **review every diff before merging**. Treat the agent's output as untrusted and gate it through normal code review.

## Recommended sandbox: Claude Code devcontainer

For unattended runs (especially with `KATO_CLAUDE_BYPASS_PERMISSIONS=true`) the recommended isolation layer is [Claude Code's devcontainer](https://code.claude.com/docs/en/devcontainer): run the `claude` binary inside a container with no network and only the per-task workspace mounted in. Kato itself does not yet wire this automatically, but operators can set it up today by configuring `KATO_CLAUDE_BINARY` to a wrapper script that launches `claude` inside the devcontainer. If you are running kato unattended, this is the layer that turns "advisory guardrails" into actual containment.

## Operator responsibilities

By running kato — and especially by setting `KATO_CLAUDE_BYPASS_PERMISSIONS=true` — you, the operator, accept the following:

- **You authorize the agent to act with your credentials.** Anything the kato process can reach (git remotes, ticket platforms, the local filesystem, the network, any environment variable you pass in) is reachable by the agent. There is no internal privilege boundary between kato and the agent it spawns.
- **You are responsible for the systems kato touches.** Kato is intended for use against repositories and ticket platforms you own or are explicitly authorized to modify. Do not point it at third-party systems without that authorization.
- **You are responsible for reviewing the agent's output.** Every PR kato opens must go through normal human code review before merging. The MIT no-warranty disclaimer below covers the maintainers; it does not move review responsibility off the operator.
- **You are responsible for your own sandbox.** If your use case requires network isolation, filesystem sandboxing, secret-scope reduction, or any compliance property (SOC 2, HIPAA, GDPR, export control, etc.), build that layer yourself — devcontainer, separate VM, scoped credentials — before pointing kato at production work.
- **You are responsible for what you set true.** `KATO_CLAUDE_BYPASS_PERMISSIONS=true` and any future flag of similar weight ship off-by-default. Flipping them on is an explicit operator decision, recorded in your `.env`, and surfaced in kato's logs as a `WARNING`. The decision and its consequences are yours.

Vulnerability disclosure path and the longer threat model live in [SECURITY.md](SECURITY.md).

## No warranty

Kato is provided under the [MIT License](LICENSE) — no warranty, express or implied. You run kato on your code, your repos, and your credentials at your own risk. The maintainers do not take responsibility for damage caused by the agent (a compromised model, a misconfigured environment, an exfiltrated secret, a force-pushed branch, anything else). If your use case requires guaranteed isolation or compliance properties, build that layer yourself before pointing kato at production work.
