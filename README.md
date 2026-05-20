<p align="center">
  <img src="./kato.png" alt="Kato" width="220" />
</p>

# Kato

<p align="center">
  <img src="./docs/img/bruce-lee-kato.jpg" alt="Bruce Lee as Kato in The Green Hornet (1966)" width="180" />
  <br />
  <em>Kato will help you kick all your tasks.</em>
</p>

**Kato is your autonomous coding sidekick.** Assign it a ticket in YouTrack, Jira, GitHub, GitLab, or Bitbucket — kato clones the repo, writes the code with Claude (or OpenHands), runs your tests, opens a pull request, and posts a summary back on the ticket. If reviewers leave PR comments, kato either fixes them or answers in the thread.

You stay in control: review every diff before merging, chat with the agent live through the built-in planning UI, or pause kato before it pushes anything.

---

## 5-minute start

```bash
git clone <this-repo>
cd kato

kato bootstrap     # one-time: Python venv + dependencies
kato configure     # interactive wizard for your .env (ticket platform, repos, LLM)
kato doctor        # checks your config is valid
kato up            # starts kato + opens the planning UI in your browser
```

That's it. To make kato work a ticket: open it in your tracker, **assign it to yourself**, and add the tag `kato:repo:<repo-folder-name>` (e.g. `kato:repo:my-backend`). Kato picks it up on the next 30-second scan tick.

> Want to chat with the agent instead of letting it run on its own? Add the tag `kato:wait-planning` — kato opens a chat tab for the ticket and waits for you to drive the conversation.

---

## What kato can do for you

- 🎫 **Watch your tickets** — YouTrack, Jira, GitHub Issues, GitLab Issues, or Bitbucket Issues
- 🤖 **Pick your agent** — Claude Code CLI (local) or OpenHands (HTTP), switchable with one env var
- 🌿 **Isolate every task** — fresh clone per ticket under `~/.kato/workspaces/<ticket-id>/`
- 🧪 **Run your tests** — optional dedicated testing container, or skip testing entirely
- 📬 **Open pull requests** — one PR per repo, summary auto-posted back on the ticket
- 💬 **Handle reviewer feedback** — fix the comment OR reply in the thread, kato decides
- 🔐 **Block bad work before it starts** — `.env` / secret / CVE scanner runs before the agent sees the code
- 🖥 **Watch it work live** — Planning UI (Flask + React) with chat, file tree, diffs, status bar
- ⏸ **Pause before push** — tag `kato:wait-before-git-push` and approve PR creation from the UI
- 📂 **Multi-repo tickets** — one ticket → one PR per `kato:repo:<name>` tag
- 🔔 **Notifications** — email + Slack on completion / failure

---

## Documentation map

Pick the page that matches what you're trying to do:

| If you want to… | Read |
|---|---|
| Understand what tags control kato behavior | [readmeTags.md](readmeTags.md) |
| Connect kato to your ticket tracker | [readmeIssuePlatforms.md](readmeIssuePlatforms.md) |
| Switch from OpenHands to Claude Code (or vice-versa) | [readmeAgentBackend.md](readmeAgentBackend.md) |
| Configure OpenHands with Bedrock or OpenRouter | [readmeOpenHands.md](readmeOpenHands.md) |
| See every env var kato reads | [readmeEnvironmentReference.md](readmeEnvironmentReference.md) |
| Walk through full setup / Docker Compose / manual flow | [readmeHowToUse.md](readmeHowToUse.md) — or the shorter [SETUP.md](SETUP.md) |
| Rebuild the Planning UI / clean stuck state | [readmePlanningUI.md](readmePlanningUI.md) |
| Understand the security model + your responsibilities | [readmeSecurity.md](readmeSecurity.md) |
| Run the test suite | [readmeTesting.md](readmeTesting.md) |
| Debug a problem or cut LLM cost | [readmeTroubleshooting.md](readmeTroubleshooting.md) |
| See how kato is built (architecture, flows, layering) | [readmeArchitecture.md](readmeArchitecture.md) — or the deeper [architecture.md](architecture.md) |
| Report a vulnerability / read the threat model | [SECURITY.md](SECURITY.md) |
| Read the bypass-permissions defense layers | [BYPASS_PROTECTIONS.md](BYPASS_PROTECTIONS.md) |
| Contribute code | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Give the agent project-specific coding rules | [AGENTS.md](AGENTS.md) |
| Adopt an existing Claude session | [ADOPTING_EXISTING_CLAUDE_SESSIONS.md](ADOPTING_EXISTING_CLAUDE_SESSIONS.md) |

---

## Safety in one paragraph

Kato runs three gates before any agent touches your code: a hard **repository denylist**, a **pre-execution security scanner** (`detect-secrets`, `bandit`, `safety`, `npm audit`, committed `.env` checker), and the **Restricted Execution Protocol** — kato refuses to act on any repo you haven't explicitly approved with `./kato approve-repo`. The agent's tool calls go through a per-tool **Approve / Deny** modal in the Planning UI by default. If you flip that off (`KATO_CLAUDE_BYPASS_PERMISSIONS=true`), kato refuses to start under root, under CI/Docker/cron, and double-prompts you on every interactive boot — see [readmeSecurity.md](readmeSecurity.md) and [BYPASS_PROTECTIONS.md](BYPASS_PROTECTIONS.md). Even with all of this, the real safety net is the same one you use for humans: **review every diff before merging.**

---

## Why "Kato"?

The name comes from Kato, the Green Hornet's sidekick, famously played by Bruce Lee. That makes it a fitting name for this project: a helper that works alongside the main mission, stays useful in the background, and helps get important work done.

I love and respect Bruce Lee, and I wanted the name to reflect that admiration.

---

## License

MIT — no warranty. You run kato on your code, your repos, and your credentials at your own risk. The maintainers do not take responsibility for damage caused by the agent. If your use case needs guaranteed isolation or compliance (SOC 2, HIPAA, GDPR, export control), build that layer yourself before pointing kato at production work. See [LICENSE](LICENSE) and the longer note in [readmeSecurity.md](readmeSecurity.md).
