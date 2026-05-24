"""The Protocol every agent backend implements.

Two impls today (claude_core_lib, openhands_core_lib) and a
hypothetical third (codex_core_lib) all surface the same eight
operations: a connection / model-access health check, the four
real-work entry points (implement, test, fix one comment, fix many
comments), and two lifecycle ops for cleaning up agent
conversations.

Why ``Protocol`` and not ``ABC``: the existing backends already
match this shape via duck typing (this contract was reverse-
engineered from their byte-identical signatures). Protocol means
new backends opt in just by matching the methods, no inheritance
needed — same approach ``vcs_provider_contracts`` takes for the
issue / pull-request providers.

Streaming-vs-RPC asymmetry: this contract describes the
**autonomous** call surface kato uses for the scan loop. The
streaming session protocol the planning UI uses (long-lived
process, NDJSON events, in-flight permission asks) is
**Claude-specific** — it lives on ``claude_core_lib``'s
``StreamingClaudeSession`` directly, not on this Protocol.
OpenHands has no equivalent because its runtime model is HTTP
RPC. Forcing both to share a streaming interface would either
warp the contract or strand OpenHands; keeping the streaming
surface off the Protocol respects the real difference.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_provider_contracts.agent_provider_contracts.agent_result import (
    AgentResult,
)
from agent_provider_contracts.agent_provider_contracts.agent_review_comment import (
    AgentReviewComment,
)
from agent_provider_contracts.agent_provider_contracts.agent_task import AgentTask
from agent_provider_contracts.agent_provider_contracts.prepared_task_context import (
    AgentPreparedTaskContext,
)


@runtime_checkable
class AgentProvider(Protocol):
    def validate_connection(self) -> None:
        """Probe the backend reachable + credentialed at startup.

        Called once at boot from kato's startup validators. Raises
        on misconfiguration (bad token, unreachable host, missing
        binary) so the operator gets a clear refusal instead of a
        first-task failure.
        """
        ...

    def validate_model_access(self) -> None:
        """Probe the configured model is callable for this account.

        Separate from ``validate_connection`` because credentials
        can be valid (the API answers) while the configured model
        is gated / unprovisioned for the calling account.
        """
        ...

    def implement_task(
        self,
        task: AgentTask,
        agent_session_id: str = '',
        prepared_task: AgentPreparedTaskContext | None = None,
    ) -> AgentResult:
        """Run the agent against a task to produce code changes.

        ``agent_session_id`` is the previous run's id when resuming a
        task; empty for fresh runs. The result dict always carries
        ``success: bool`` plus per-backend diagnostic keys.
        """
        ...

    def test_task(
        self,
        task: AgentTask,
        prepared_task: AgentPreparedTaskContext | None = None,
    ) -> AgentResult:
        """Run a verification pass after ``implement_task``.

        Backends decide what counts as testing (unit suite, lint,
        type check, smoke run) — this is just kato asking the
        backend to verify its own work.
        """
        ...

    def fix_review_comment(
        self,
        comment: AgentReviewComment,
        branch_name: str,
        agent_session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
    ) -> AgentResult:
        """Run the agent against a single review comment.

        Convenience over ``fix_review_comments([comment], ...)``
        for the one-comment hotpath.
        """
        ...

    def fix_review_comments(
        self,
        comments: list[AgentReviewComment],
        branch_name: str,
        agent_session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
        mode: str = 'fix',
    ) -> AgentResult:
        """Run the agent against a batch of review comments.

        ``mode='fix'`` is the standard write-the-fix flow;
        ``mode='answer'`` is the question-only flow that posts a
        plain-text reply instead of code changes.
        """
        ...

    def delete_conversation(self, conversation_id: str) -> None:
        """Tear down a single conversation/session by id.

        Used when kato needs to free agent-side resources (an
        OpenHands container, a Claude session entry) tied to a
        finished task.
        """
        ...

    def stop_all_conversations(self) -> None:
        """Tear down every active conversation.

        Called on kato shutdown so the agent backend doesn't leak
        running containers / processes / sessions.
        """
        ...
