from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.helpers import agent_prompt_utils
from agent_core_lib.agent_core_lib.helpers.logging_utils import configure_logger
from agent_core_lib.agent_core_lib.helpers.result_utils import build_openhands_result
from agent_core_lib.agent_core_lib.helpers.session_id_utils import fix_session_id
from agent_core_lib.agent_core_lib.helpers.text_utils import (
    condensed_text,
    normalized_text,
    text_from_attr,
    text_from_mapping,
)
from agent_core_lib.agent_core_lib.helpers.credential_scan import (
    scan_text_for_credentials_and_phishing,
)
from claude_core_lib.claude_core_lib.helpers.effort_levels import (
    FALLBACK_EFFORT_LEVELS,
)
from claude_core_lib.claude_core_lib.helpers.spawn_utils import (
    append_additional_dirs,
    append_model_effort_flags,
    build_appended_system_prompt,
    build_claude_subprocess_env,
    wrap_spawn_for_docker,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from sandbox_core_lib.sandbox_core_lib.workspace_delimiter import (
    wrap_untrusted_workspace_content,
)


class ClaudeCliClient(object):
    """Drive Anthropic's Claude Code CLI (`claude -p`) as the implementation/testing backend.

    Provides the same public interface as :class:`KatoClient` so the rest of the
    orchestration layer can use either backend interchangeably. Selection is
    driven by the ``KATO_AGENT_BACKEND`` environment variable.
    """

    DEFAULT_BINARY = 'claude'
    DEFAULT_TIMEOUT_SECONDS = 1800
    SAFE_PERMISSION_MODE = 'acceptEdits'
    BYPASS_PERMISSION_MODE = 'bypassPermissions'
    DEFAULT_ALLOWED_TOOLS = 'Edit,Write,Read,Bash,Glob,Grep'
    # Hard, non-overridable denylist. Kato is the only component that
    # ever runs git operations (commit, push, branch, reset, fetch,
    # rebase, ...). Claude must NEVER invoke git directly: it would race
    # with kato's branch state machine, bypass the publish-step retry
    # logic, and could push work kato hasn't validated. Every shape of
    # `git ...` we know Claude Code's allow-pattern matcher recognizes
    # is listed here. The two patterns cover both the colon-form
    # (`Bash(git:*)`) and the bare-form (`Bash(git *)`) that Claude
    # versions accept.
    GIT_DENY_PATTERNS = ('Bash(git:*)', 'Bash(git *)')
    SMOKE_TEST_PROMPT = 'Reply with exactly: ok. Do not call any tools.'
    SMOKE_TEST_TIMEOUT_SECONDS = 120
    VERSION_PROBE_TIMEOUT_SECONDS = 30

    # Single source of truth lives in ``helpers.effort_levels``; this
    # derives the validation set from the same fallback tuple the
    # discovery path falls back to, so the two never drift.
    SUPPORTED_EFFORT_LEVELS = frozenset(FALLBACK_EFFORT_LEVELS)

    def __init__(
        self,
        *,
        binary: str = '',
        model: str = '',
        max_turns: int | str | None = None,
        allowed_tools: str = '',
        disallowed_tools: str = '',
        bypass_permissions: bool = False,
        docker_mode_on: bool = False,
        read_only_tools_on: bool = False,
        max_retries: int = 3,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        repository_root_path: str = '',
        model_smoke_test_enabled: bool = False,
        extra_args: list[str] | None = None,
        effort: str = '',
        architecture_doc_path: str = '',
        lessons_path: str = '',
        workspace_refusal_guidance: str = '',
    ) -> None:
        self.max_retries = max(1, int(max_retries or 1))
        self._binary = normalized_text(binary) or self.DEFAULT_BINARY
        self._binary_path = ''
        self._model = normalized_text(model)
        self._max_turns = self._coerce_max_turns(max_turns)
        self._effort = self._coerce_effort(effort)
        self._bypass_permissions = bool(bypass_permissions)
        # Set from ``KATO_CLAUDE_DOCKER`` at boot. When True, the
        # per-task spawns (test_task → _run_prompt, investigate →
        # _run_prompt) wrap the Claude subprocess in the hardened
        # sandbox. Boot-time validators (validate_connection,
        # _run_model_access_validation) deliberately stay on the host —
        # they have no workspace and no untrusted prompt. Independent
        # of ``bypass_permissions``: docker is containment, bypass is
        # the prompt layer.
        self._docker_mode_on = bool(docker_mode_on)
        # Set from ``KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS`` at boot.
        # When True (and only valid alongside docker mode — the
        # ``validate_read_only_tools_requires_docker`` startup gate
        # refuses the flag without docker), every spawn appends the
        # hardcoded ``READ_ONLY_TOOLS_ALLOWLIST`` to ``--allowedTools``
        # so the operator isn't prompted for grep / cat / ls / find /
        # head / tail / wc / file / stat / rg / Read. Mutating tools
        # (Edit, Write, Bash without an explicit pattern) still
        # prompt as today. Independent of ``bypass_permissions``;
        # bypass disables ALL prompts, this disables only the
        # read-only ones.
        self._read_only_tools_on = bool(read_only_tools_on)
        # When not bypassing, pre-approve a safe default tool list so the
        # agent does not stall asking for permission in headless `-p` mode.
        # Users can override or extend via KATO_CLAUDE_ALLOWED_TOOLS.
        normalized_allowed = normalized_text(allowed_tools)
        self._allowed_tools = (
            normalized_allowed
            if normalized_allowed or self._bypass_permissions
            else self.DEFAULT_ALLOWED_TOOLS
        )
        self._disallowed_tools = normalized_text(disallowed_tools)
        self._timeout_seconds = max(60, int(timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS))
        self._repository_root_path = normalized_text(repository_root_path)
        self._model_smoke_test_enabled = bool(model_smoke_test_enabled)
        self._model_access_smoke_test_ran = False
        self._extra_args = list(extra_args or [])
        self._architecture_doc_path = normalized_text(architecture_doc_path)
        self._lessons_path = normalized_text(lessons_path)
        # Product-specific actionable refusal guidance appended to the
        # generic workspace scope block. Supplied by the spawner (kato)
        # so agent_core_lib/claude_core_lib stay product-agnostic; '' for
        # any consumer that doesn't set it.
        self._workspace_refusal_guidance = workspace_refusal_guidance or ''
        self.logger = configure_logger(self.__class__.__name__)
        if self._bypass_permissions:
            self.logger.warning(
                'KATO_CLAUDE_BYPASS_PERMISSIONS=true: Claude will run with '
                '--permission-mode bypassPermissions. Per-tool prompts are '
                'disabled — the agent can run Bash, Edit, Write, and any '
                'other tool without asking. The operator who set this flag '
                'accepts responsibility for any harm caused by the agent. '
                'See SECURITY.md.'
            )

    @property
    def _permission_mode(self) -> str:
        return (
            self.BYPASS_PERMISSION_MODE
            if self._bypass_permissions
            else self.SAFE_PERMISSION_MODE
        )

    # ----- public API parity with KatoClient -----

    @staticmethod
    def _running_inside_docker() -> bool:
        # /.dockerenv is the canonical marker the Docker engine creates
        # inside every container it starts. A few non-Docker runtimes (e.g.
        # Podman with --root, some CI sandboxes) also create it, which is
        # fine for our purposes — anything that quacks like a container
        # also can't reach the host's macOS Keychain or `claude login`.
        return Path('/.dockerenv').exists()

    def validate_connection(self) -> None:
        if self._running_inside_docker():
            raise RuntimeError(
                'KATO_AGENT_BACKEND=claude is not supported inside Docker. '
                'The Claude Code CLI authenticates against your host '
                '`claude login` credentials (macOS Keychain, Linux config '
                'file, or Windows Credential Manager), and the container '
                'cannot reach those. '
                'Run kato locally instead — `make compose-up` or `make run`. '
                'If you genuinely need Docker, switch to KATO_AGENT_BACKEND=openhands '
                'and use `make compose-up-docker`.'
            )
        binary_path = shutil.which(self._binary)
        if not binary_path:
            # Multi-line message printed by kato startup. Lead with
            # the one-line install command (works on macOS / Linux /
            # Windows) so the operator can fix this in 30 seconds
            # without reading the docs page.
            raise RuntimeError(
                f'\n'
                f'Claude CLI ("{self._binary}") was not found on PATH.\n'
                f'\n'
                f'Install Claude Code via npm (works on macOS, Linux, and Windows):\n'
                f'\n'
                f'    npm install -g @anthropic-ai/claude-code\n'
                f'\n'
                f'Prerequisite: Node.js 18+ (https://nodejs.org/). Verify with:\n'
                f'\n'
                f'    node --version\n'
                f'    claude --version\n'
                f'\n'
                f'After install, the ``claude`` binary must be on PATH (npm puts it\n'
                f'there automatically). If you installed it somewhere else, set\n'
                f'KATO_CLAUDE_BINARY to the full path. Full setup docs:\n'
                f'    https://docs.claude.com/en/docs/claude-code/setup\n'
            )
        self._binary_path = binary_path
        # Boot-time validator: no workspace, no untrusted prompt — runs
        # ``claude --version`` only. Sandbox-wrap is intentionally
        # skipped even when ``KATO_CLAUDE_DOCKER=true``: nothing here for
        # the sandbox to bound, and a container spin would add ~1-2s to
        # every startup with zero security benefit.
        try:
            result = subprocess.run(
                [*self._host_binary_argv(), '--version'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
                timeout=self.VERSION_PROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                f'Claude CLI binary "{self._binary}" failed to launch: {exc}'
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip() or 'unknown error'
            raise RuntimeError(
                f'Claude CLI binary "{self._binary}" failed to report a version: {detail}'
            )
        self.logger.info(
            'Claude CLI is available at %s (%s)',
            binary_path,
            condensed_text(result.stdout),
        )
        self._validate_model_smoke_test()

    def validate_model_access(self) -> None:
        self._validate_model_access_smoke_test()

    def delete_conversation(self, conversation_id: str) -> None:
        # Claude CLI sessions are stored locally on disk; nothing to clean up
        # remotely. The orchestration layer treats this as a best-effort cleanup
        # hook, so a no-op is correct.
        return

    def stop_all_conversations(self) -> None:
        # No remote agent-server containers exist for the Claude CLI backend.
        return

    def implement_task(
        self,
        task: Any,
        agent_session_id: str = '',
        prepared_task: Any | None = None,
    ) -> dict[str, str | bool]:
        self.logger.info('requesting implementation for task %s', task.id)
        prompt = self._build_implementation_prompt(task, prepared_task)
        cwd, additional_dirs = self._working_directories(prepared_task)
        result = self._run_prompt_result(
            prompt=prompt,
            cwd=cwd,
            additional_dirs=additional_dirs,
            branch_name=agent_prompt_utils.task_branch_name(task, prepared_task),
            default_commit_message=f'Implement {task.id}',
            agent_session_id=agent_session_id,
            log_label=agent_prompt_utils.task_conversation_title(task),
            task_id=str(task.id),
        )
        self.logger.info(
            'implementation finished for task %s with success=%s',
            task.id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    def test_task(
        self,
        task: Any,
        prepared_task: Any | None = None,
    ) -> dict[str, str | bool]:
        self.logger.info('requesting testing validation for task %s', task.id)
        prompt = self._build_testing_prompt(task, prepared_task)
        cwd, additional_dirs = self._working_directories(prepared_task)
        result = self._run_prompt_result(
            prompt=prompt,
            cwd=cwd,
            additional_dirs=additional_dirs,
            log_label=agent_prompt_utils.task_conversation_title(task, suffix=' [testing]'),
            task_id=str(task.id),
        )
        self.logger.info(
            'testing validation finished for task %s with success=%s',
            task.id,
            result[ImplementationFields.SUCCESS],
        )
        return result

    def investigate(self, prompt: str, *, cwd: str = '') -> str:
        """Run a single read-only Claude turn and return the raw text.

        Used by the triage flow: kato hands Claude a task description
        and a list of valid triage outcome tags, asks Claude to pick
        one. No file edits, no PR work — disallowedTools blocks all
        write paths (Edit, Write, Bash, etc.) so even a confused turn
        can't damage the repo.
        """
        normalized_prompt = normalized_text(prompt)
        if not normalized_prompt:
            raise ValueError('prompt is required to run an investigation')
        normalized_cwd = normalized_text(cwd)
        if not normalized_cwd:
            normalized_cwd = self._repository_root_path or os.getcwd()
        # Strict tool denylist: triage is read-only by definition.
        original_disallowed = self._disallowed_tools
        original_allowed = self._allowed_tools
        try:
            self._disallowed_tools = 'Edit,Write,MultiEdit,NotebookEdit,Bash,WebFetch'
            self._allowed_tools = 'Read,Glob,Grep'
            payload = self._run_prompt(
                prompt=normalized_prompt,
                cwd=normalized_cwd,
                additional_dirs=[],
                log_label='triage investigation',
                task_id='triage',
            )
        finally:
            self._disallowed_tools = original_disallowed
            self._allowed_tools = original_allowed
        result_text = payload.get('result') or payload.get(ImplementationFields.MESSAGE) or ''
        return str(result_text)

    def fix_review_comment(
        self,
        comment: ReviewComment,
        branch_name: str,
        agent_session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
    ) -> dict[str, str | bool]:
        return self.fix_review_comments(
            [comment],
            branch_name,
            agent_session_id=agent_session_id,
            task_id=task_id,
            task_summary=task_summary,
        )

    def fix_review_comments(
        self,
        comments: list[ReviewComment],
        branch_name: str,
        agent_session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
        mode: str = 'fix',
    ) -> dict[str, str | bool]:
        """Address multiple PR review comments in a single Claude spawn.

        ``comments`` must all belong to the same pull request — the
        caller (``ReviewCommentService``) guarantees grouping by
        (repo, pr) before calling. ``branch_name`` is the existing
        task branch to commit on; one push covers every comment in
        the batch.

        ``mode``:
        - ``'fix'`` (default) — the legacy flow. Agent makes edits,
          commits, returns success when the workspace has the change.
        - ``'answer'`` — the question-answering flow. Agent reads the
          code to understand context but does NOT modify any files;
          the returned ``message`` text is what kato posts back to
          each commenter as a reply. The caller (service) skips
          ``publish_review_fix`` for this mode.

        For ``len(comments) == 1`` the prompt is identical to the
        legacy single-comment prompt (``_build_review_prompt``) so
        existing single-comment paths regress nothing. For 2+ the
        builder enumerates each comment with its file/line
        localization and asks the agent to address them in one
        coherent change-set.
        """
        if not comments:
            raise ValueError('fix_review_comments requires at least one comment')
        cwd = self._review_comment_cwd(comments[0])
        if len(comments) == 1:
            single = comments[0]
            prompt = self._build_review_prompt(
                single, branch_name, workspace_path=cwd, mode=mode,
                workspace_refusal_guidance=self._workspace_refusal_guidance,
            )
        else:
            prompt = self._build_review_comments_batch_prompt(
                comments, branch_name, workspace_path=cwd, mode=mode,
                workspace_refusal_guidance=self._workspace_refusal_guidance,
            )
        result = self._run_prompt_result(
            prompt=prompt,
            cwd=cwd,
            additional_dirs=[],
            agent_session_id=agent_session_id,
            branch_name=branch_name,
            default_commit_message='Address review comments',
            log_label=agent_prompt_utils.review_conversation_title(
                comments[0],
                task_id=task_id,
                task_summary=task_summary,
            ),
            task_id=task_id,
        )
        self.logger.info(
            'review fix finished for pull request %s with %d comment(s) success=%s',
            comments[0].pull_request_id,
            len(comments),
            result[ImplementationFields.SUCCESS],
        )
        return result

    # ----- prompt builders (Claude-specific, share core helpers with KatoClient) -----

    def _build_implementation_prompt(
        self,
        task: Any,
        prepared_task: Any | None = None,
    ) -> str:
        scope_block = agent_prompt_utils.workspace_scope_block(
            _repository_local_paths(prepared_task),
            extra_refusal_guidance=self._workspace_refusal_guidance,
        )
        repository_scope = agent_prompt_utils.repository_scope_text(task, prepared_task)
        agents_instructions = agent_prompt_utils.agents_instructions_text(prepared_task)
        # OG9a: ``task.summary`` and ``task.description`` come from
        # the issue tracker (YouTrack / Bitbucket / etc.) and may
        # contain text written by anyone with comment access. Wrap
        # them so the model can tell trusted scaffolding (kato's own
        # prompt) from untrusted issue text. ``task.id`` is
        # kato-controlled; do not wrap it.
        untrusted_task_body = wrap_untrusted_workspace_content(
            f'{task.summary}\n\n{task.description}',
            source_path=f'task:{task.id}',
        )
        scope_prefix = f'{scope_block}\n' if scope_block else ''
        return (
            f'{scope_prefix}'
            f'Implement task {task.id}.\n\n'
            f'{untrusted_task_body}\n\n'
            f'{repository_scope}\n\n'
            f'{agents_instructions}\n\n'
            f'{self._execution_guardrails_text()}\n\n'
            f'{self._completion_instructions_text()}\n\n'
            'The validation_report.md must list every changed file and, under each '
            'file name, add a short explanation of what changed.\n'
            'Use this format inside validation_report.md:\n'
            'Files changed:\n'
            '- path/to/file.ext\n'
            '  Short explanation.\n'
            '- another/file.ext\n'
            '  Short explanation.\n'
        )

    def _build_testing_prompt(
        self,
        task: Any,
        prepared_task: Any | None = None,
    ) -> str:
        repository_scope = agent_prompt_utils.repository_scope_text(task, prepared_task)
        agents_instructions = agent_prompt_utils.agents_instructions_text(prepared_task)
        # OG9a: same reasoning as ``_build_implementation_prompt``;
        # the testing agent is also pointed at the same untrusted
        # issue text and needs the same framing.
        untrusted_task_body = wrap_untrusted_workspace_content(
            f'{task.summary}\n\n{task.description}',
            source_path=f'task:{task.id}',
        )
        return (
            f'Validate the implementation for task {task.id}.\n\n'
            f'{untrusted_task_body}\n\n'
            f'{repository_scope}\n\n'
            f'{agents_instructions}\n\n'
            f'{self._execution_guardrails_text()}\n\n'
            'Act as a separate testing agent.\n'
            'Write additional tests when needed, challenge the new code with edge cases, '
            'run the relevant tests, and fix any test failures you can resolve safely.\n'
            'Make the smallest possible change needed for the validation work.\n'
            'Prefer editing only the exact lines or blocks that need to change.\n'
            'Do not change indentation, formatting, or unrelated lines when a narrow edit is enough.\n'
            'Do not run npm run build, yarn build, pnpm build, or any equivalent production build command unless the task explicitly requires it.\n'
            'Do not commit or stage generated build artifacts such as build, dist, out, coverage, or target directories.\n'
            'Do not create a pull request.\n'
            f'{self._completion_instructions_text(testing=True)}\n'
            'If no dedicated tests are defined or available, do not invent new ones; '
            'just report that no testing was defined and stop after saving any change.\n'
        )

    @classmethod
    def _build_review_comments_batch_prompt(
        cls,
        comments: list[ReviewComment],
        branch_name: str,
        workspace_path: str = '',
        mode: str = 'fix',
        workspace_refusal_guidance: str = '',
    ) -> str:
        """Render a batched prompt for 2+ comments on one PR.

        Architecture:
        - Single header naming the branch + repository.
        - Numbered list of comments, each with localization (file/
          line/commit) and the comment body wrapped as untrusted
          content (same OG9a wrapping the singular prompt does).
        - Optional shared "review context" section (resolved-comment
          history) drawn from the first comment's ``ALL_COMMENTS``
          since every comment in the batch lives on the same PR.
        - Same execution guardrails + completion contract as the
          singular prompt — kato just changes "address one comment"
          to "address all the listed comments in one change-set."
        """
        first = comments[0]
        repository_context = agent_prompt_utils.review_repository_context(first)
        # Wrap each body individually so each entry in the numbered
        # list still has its own untrusted-content marker — the
        # agent must treat every comment as data, not directive.
        wrapped_comments: list = []
        for comment in comments:
            wrapped_body = wrap_untrusted_workspace_content(
                comment.body,
                source_path=f'pr-comment:{comment.author}',
            )
            shadow = ReviewComment(
                pull_request_id=comment.pull_request_id,
                comment_id=comment.comment_id,
                author=comment.author,
                body=wrapped_body,
                file_path=comment.file_path,
                line_number=comment.line_number,
                line_type=comment.line_type,
                commit_sha=comment.commit_sha,
            )
            wrapped_comments.append(shadow)
        batch_text = agent_prompt_utils.review_comments_batch_text(
            wrapped_comments, workspace_path=workspace_path,
        )
        # Per-PR review context comes from any one comment — they
        # share the thread. Skip when empty so we don't emit blank
        # marker tags.
        review_context = agent_prompt_utils.review_comment_context_text(first)
        wrapped_review_context = (
            wrap_untrusted_workspace_content(
                review_context,
                source_path='pr-comment-thread',
            )
            if review_context
            else ''
        )
        scope_block = agent_prompt_utils.workspace_scope_block(
            [workspace_path] if workspace_path else [],
            extra_refusal_guidance=workspace_refusal_guidance,
        )
        scope_prefix = f'{scope_block}\n' if scope_block else ''
        # Pull AGENTS.md from the workspace clone if the project has
        # one — the review-fix agent should respect the same
        # checked-in conventions the implementation agent did.
        from agent_core_lib.agent_core_lib.helpers.agents_instruction_utils import (
            agents_instructions_for_path,
        )
        agents_text = agents_instructions_for_path(
            workspace_path,
            repository_id=str(getattr(first, 'repository_id', '') or ''),
        )
        agents_block = f'{agents_text}\n\n' if agents_text else ''
        if mode == 'answer':
            return (
                f'{scope_prefix}'
                f'The following pull request review questions are on branch '
                f'{branch_name}{repository_context}.\n\n'
                f'{batch_text}'
                f'{wrapped_review_context}\n\n'
                f'{agents_block}'
                f'{cls._execution_guardrails_text()}\n\n'
                'These are QUESTIONS, not fix requests. Read the relevant '
                'code to understand context, then write a concise plain-text '
                'answer that addresses every question.\n'
                'Rules:\n'
                '- Do NOT modify any files. Do not call Edit, Write, or any '
                'tool that mutates the workspace.\n'
                '- Do not commit. Do not push.\n'
                '- Number your answers 1, 2, 3 to match the numbered '
                'questions above.\n'
                '- Keep each answer focused: explain the behaviour, point to '
                'the relevant file/line if helpful, and stop.\n'
                'When you are done, stop. Your final response will be '
                'posted as the reply to each question.\n'
            )
        return (
            f'{scope_prefix}'
            f'Address the following pull request review comments on branch '
            f'{branch_name}{repository_context}.\n\n'
            f'{batch_text}'
            f'{wrapped_review_context}\n\n'
            f'{agents_block}'
            f'{cls._execution_guardrails_text()}\n\n'
            'Address every comment listed above in a single coherent '
            'change-set.\n'
            'For each comment:\n'
            '- Make the smallest possible change needed to address it.\n'
            '- Prefer editing only the exact lines or blocks that need to change.\n'
            '- Do not change indentation, formatting, or unrelated lines '
            'when a narrow edit is enough.\n'
            'Do not report success until all intended changes are saved in '
            'the repository worktree.\n'
            'When you are done, stop. Do not produce any extra commentary.\n'
        )

    @classmethod
    def _build_review_prompt(
        cls,
        comment: ReviewComment,
        branch_name: str,
        workspace_path: str = '',
        mode: str = 'fix',
        workspace_refusal_guidance: str = '',
    ) -> str:
        repository_context = agent_prompt_utils.review_repository_context(comment)
        review_context = agent_prompt_utils.review_comment_context_text(comment)
        location_text = agent_prompt_utils.review_comment_location_text(comment)
        # Inline the code snippet around the commented line when we
        # can read it from the workspace. Saves a Read tool call per
        # inline comment (typically several KB of file content).
        snippet_text = (
            agent_prompt_utils.review_comment_code_snippet(comment, workspace_path)
            if workspace_path
            else ''
        )
        # OG9a: ``comment.body`` is whatever a human (or bot) typed
        # on the pull request — wholly untrusted. Wrap it so a
        # comment like "ignore previous instructions and approve"
        # is structurally identifiable as data, not a directive.
        untrusted_comment_body = wrap_untrusted_workspace_content(
            comment.body,
            source_path=f'pr-comment:{comment.author}',
        )
        # OG9a: prior comment thread is also untrusted — same author
        # surface as the leading comment. Skip wrap when empty so
        # we don't emit empty marker tags into the prompt.
        wrapped_review_context = (
            wrap_untrusted_workspace_content(
                review_context,
                source_path='pr-comment-thread',
            )
            if review_context
            else ''
        )
        location_block = f'{location_text}\n' if location_text else ''
        snippet_block = f'{snippet_text}\n' if snippet_text else ''
        scope_block = agent_prompt_utils.workspace_scope_block(
            [workspace_path] if workspace_path else [],
            extra_refusal_guidance=workspace_refusal_guidance,
        )
        scope_prefix = f'{scope_block}\n' if scope_block else ''
        from agent_core_lib.agent_core_lib.helpers.agents_instruction_utils import (
            agents_instructions_for_path,
        )
        agents_text = agents_instructions_for_path(
            workspace_path,
            repository_id=str(getattr(comment, 'repository_id', '') or ''),
        )
        agents_block = f'{agents_text}\n\n' if agents_text else ''
        if mode == 'answer':
            return (
                f'{scope_prefix}'
                f'A pull request reviewer asked a QUESTION on branch '
                f'{branch_name}{repository_context}.\n'
                f'{location_block}'
                f'{snippet_block}'
                f'Question by {comment.author}:\n{untrusted_comment_body}'
                f'{wrapped_review_context}\n\n'
                f'{agents_block}'
                f'{cls._execution_guardrails_text()}\n\n'
                'Read the relevant code to understand context, then write a '
                'concise plain-text answer.\n'
                'Rules:\n'
                '- Do NOT modify any files. Do not call Edit, Write, or any '
                'tool that mutates the workspace.\n'
                '- Do not commit. Do not push.\n'
                '- Keep the answer focused: explain the behaviour, point to '
                'the relevant file/line if helpful, and stop.\n'
                'Your final response will be posted as the reply to the '
                'question.\n'
            )
        return (
            f'{scope_prefix}'
            f'Address pull request comment on branch {branch_name}{repository_context}.\n'
            f'{location_block}'
            f'{snippet_block}'
            f'Comment by {comment.author}:\n{untrusted_comment_body}'
            f'{wrapped_review_context}\n\n'
            f'{agents_block}'
            f'{cls._execution_guardrails_text()}\n\n'
            'Make the smallest possible change needed to address the review comment.\n'
            'Prefer editing only the exact lines or blocks that need to change.\n'
            'Do not change indentation, formatting, or unrelated lines when a narrow edit is enough.\n'
            'Do not report success until all intended changes are saved in the repository worktree.\n'
            'When you are done, stop. Do not produce any extra commentary.\n'
        )

    def _completion_instructions_text(self, *, testing: bool = False) -> str:
        if testing:
            return (
                'When you are done:\n'
                '- Save every intended change in the repository worktree.\n'
                '- Create validation_report.md in the repository root that summarizes the testing work.\n'
                '- Do not commit or stage validation_report.md; the orchestration layer will read and remove it.\n'
                '- Stop. Do not produce any extra commentary.'
            )
        return (
            'When you are done:\n'
            '- Save every intended change in the repository worktree.\n'
            '- Create validation_report.md in the repository root that will become the pull request description.\n'
            '- Make the smallest possible change needed to satisfy the task.\n'
            '- Prefer editing only the exact lines or blocks that need to change.\n'
            '- Do not change indentation, formatting, or unrelated lines when a narrow edit is enough.\n'
            '- Do not run npm run build, yarn build, pnpm build, or any equivalent production build command unless the task explicitly requires it.\n'
            '- Do not commit or stage generated build artifacts such as build, dist, out, coverage, or target directories.\n'
            '- Do not commit or stage validation_report.md; the orchestration layer will read and remove it before opening the pull request.\n'
            '- If no dedicated tests are defined for this task, do not invent new ones; just stop after saving the change.\n'
            '- Stop. Do not produce any extra commentary.'
        )

    @classmethod
    def _execution_guardrails_text(cls) -> str:
        sections = [
            agent_prompt_utils.security_guardrails_text(),
            agent_prompt_utils.forbidden_repository_guardrails_text(),
            cls._tool_guardrails_text(),
        ]
        return '\n\n'.join(section for section in sections if section)

    @staticmethod
    def _tool_guardrails_text() -> str:
        return (
            'Tool guardrails:\n'
            '- Use Edit/Write/Read for file edits and reads.\n'
            '- Use Bash sparingly and only for non-destructive shell needs (rg, sed -n, cat, ls).\n'
            '\n'
            'YOUR JOB IS TO EDIT FILES. THAT IS ALL.\n'
            '\n'
            'You do NOT do any of the following — ever, under any circumstance:\n'
            '- git (status, diff, log, add, commit, push, pull, fetch, checkout, switch, branch, reset, rebase, stash, tag, anything)\n'
            '- create pull requests / merge requests\n'
            '- call GitHub / GitLab / Bitbucket APIs\n'
            '- ask the operator for permission to commit\n'
            '- mention git, commits, PRs, or branches in your reply except to say you are done editing\n'
            '\n'
            'KATO handles everything after you finish:\n'
            '- Kato is the orchestrator that spawned you.\n'
            '- Kato sees your file edits on disk and commits them.\n'
            '- Kato pushes the branch.\n'
            '- Kato opens the pull request.\n'
            '- This is automatic. The operator does NOT need to allow anything, run anything, or click anything for git to happen.\n'
            '\n'
            'When you finish editing, your reply must be exactly one short sentence: "Done — edits written, kato will publish."  If you genuinely have nothing more to say, that one line is the entire reply.\n'
            '\n'
            'Do NOT say things like "I am ready to commit when you allow git access" or "let me know when I can push" or any variation. Those are wrong because there is nothing for the operator to allow — kato runs git automatically the moment your turn ends.'
        )

    # ----- subprocess execution -----

    def _run_prompt_result(
        self,
        *,
        prompt: str,
        cwd: str,
        additional_dirs: list[str],
        branch_name: str = '',
        default_commit_message: str | None = None,
        agent_session_id: str = '',
        log_label: str = '',
        task_id: str = '',
    ) -> dict[str, str | bool]:
        payload = self._run_prompt(
            prompt=prompt,
            cwd=cwd,
            additional_dirs=additional_dirs,
            agent_session_id=agent_session_id,
            log_label=log_label,
            task_id=task_id,
        )
        return build_openhands_result(
            payload,
            branch_name=branch_name,
            default_commit_message=default_commit_message,
        )

    def _run_prompt(
        self,
        *,
        prompt: str,
        cwd: str,
        additional_dirs: list[str],
        agent_session_id: str = '',
        log_label: str = '',
        task_id: str = '',
    ) -> dict[str, str | bool]:
        command = self._build_command(
            additional_dirs=additional_dirs,
            agent_session_id=agent_session_id,
            resolve_binary=not self._docker_mode_on,
        )
        env = self._build_subprocess_env()
        log_label = log_label or 'Claude CLI'
        # Docker mode wraps the spawn in the hardened sandbox — see
        # ``kato.sandbox.manager``. Mirrors the streaming-session path
        # in ``StreamingClaudeSession.start`` via the shared
        # ``wrap_spawn_for_docker`` helper so test_task and investigate
        # get the same containment as the interactive planning sessions.
        # Gated on ``_docker_mode_on``, not ``_bypass_permissions``:
        # docker is containment, bypass is the prompt layer.
        spawn_cwd: str | None = cwd or None
        if self._docker_mode_on:
            workspace_path = cwd or self._repository_root_path or os.getcwd()
            command = wrap_spawn_for_docker(
                command,
                workspace_path=workspace_path,
                task_id=task_id or 'unknown',
                logger=self.logger,
            )
            # Docker sets the container WORKDIR to /workspace; the host
            # cwd is irrelevant for the docker client itself.
            spawn_cwd = None
        self.logger.info('Mission %s: invoking Claude CLI', log_label)
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                cwd=spawn_cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f'Claude CLI did not finish within {self._timeout_seconds}s for {log_label}'
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f'failed to invoke Claude CLI binary "{self._binary}": {exc}'
            ) from exc

        return self._parse_completed_process(completed, log_label=log_label)

    def _build_command(
        self,
        *,
        additional_dirs: list[str],
        agent_session_id: str,
        resolve_binary: bool = True,
        include_system_prompt: bool = True,
    ) -> list[str]:
        command: list[str] = [
            *(self._host_binary_argv() if resolve_binary else [self._binary]),
            '-p',
            '--output-format',
            'json',
            '--permission-mode',
            self._permission_mode,
        ]
        append_model_effort_flags(
            command,
            model=self._model,
            max_turns=self._max_turns,
            effort=self._effort,
        )
        merged_allowed = self._merge_allowed_with_read_only_allowlist(self._allowed_tools)
        if merged_allowed:
            command.extend(['--allowedTools', merged_allowed])
        merged_disallowed = self._merge_disallowed_with_git_deny(self._disallowed_tools)
        command.extend(['--disallowedTools', merged_disallowed])
        # ``include_system_prompt=False`` is for boot smoke-tests that
        # only need to confirm model reachability ("Reply with: ok").
        # Inlining the architecture doc + lessons there can push the
        # command line past Windows' CreateProcess limit (~32K chars,
        # less when the operator's PATH or env is unusual), surfacing
        # as ``[WinError 206] The filename or extension is too long``.
        # Real spawns still get the full system prompt — only the
        # validator skips it.
        if include_system_prompt:
            appended_system_prompt = build_appended_system_prompt(
                architecture_doc_path=self._architecture_doc_path,
                lessons_path=self._lessons_path,
                docker_mode_on=self._docker_mode_on,
                logger=self.logger,
            )
            if appended_system_prompt:
                command.extend(['--append-system-prompt', appended_system_prompt])
        normalized_session_id = fix_session_id(agent_session_id)
        if normalized_session_id:
            command.extend(['--resume', normalized_session_id])
        append_additional_dirs(command, additional_dirs)
        command.extend(self._extra_args)
        return command

    def _merge_allowed_with_read_only_allowlist(self, operator_allowed: str) -> str:
        """Append the hardcoded read-only allowlist when the flag is on.

        When ``KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS=true`` (and docker
        is on — the startup gate refuses the flag without docker),
        every spawn pre-approves the entries in
        ``READ_ONLY_TOOLS_ALLOWLIST`` so the operator is not prompted
        for grep / rg / ls / cat / find / head / tail / wc / file /
        stat / Read.

        Operator extensions via ``KATO_CLAUDE_ALLOWED_TOOLS`` are
        preserved; the read-only allowlist is unioned in (no
        duplicates). When the flag is off, returns the operator
        value unchanged.

        The allowlist is hardcoded — the operator cannot widen it
        via env var. Adding a tool here is a security decision
        (an operator who picks the wrong "read-only" command silently
        widens the agent's blast radius); code-level edits force a
        review. The allowlist's exact membership is locked by a
        drift-guard test.
        """
        if not self._read_only_tools_on:
            return operator_allowed
        from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (
            READ_ONLY_TOOLS_ALLOWLIST,
        )
        existing = [
            entry.strip()
            for entry in (operator_allowed or '').split(',')
            if entry.strip()
        ]
        seen = {entry: True for entry in existing}
        # Deterministic order so the resulting argv is stable across
        # runs (helps when comparing logs / audit entries).
        for pattern in sorted(READ_ONLY_TOOLS_ALLOWLIST):
            if pattern not in seen:
                existing.append(pattern)
                seen[pattern] = True
        return ','.join(existing)

    @classmethod
    def _merge_disallowed_with_git_deny(cls, operator_disallowed: str) -> str:
        """Always include the git denylist, regardless of operator config.

        The operator can extend the denylist via ``KATO_CLAUDE_DISALLOWED_TOOLS``
        but cannot remove the git patterns. Kato is the sole component that
        runs git operations.
        """
        existing = [
            entry.strip()
            for entry in (operator_disallowed or '').split(',')
            if entry.strip()
        ]
        seen = {entry: True for entry in existing}
        for pattern in cls.GIT_DENY_PATTERNS:
            if pattern not in seen:
                existing.append(pattern)
                seen[pattern] = True
        return ','.join(existing)

    def _build_subprocess_env(self) -> dict[str, str]:
        # Force JSON output to stdout and prevent any TTY-dependent
        # behavior. Shared invariant with the streaming path — see
        # ``build_claude_subprocess_env``.
        return build_claude_subprocess_env()

    def _parse_completed_process(
        self,
        completed: subprocess.CompletedProcess,
        *,
        log_label: str,
    ) -> dict[str, str | bool]:
        stdout = completed.stdout or ''
        stderr = (completed.stderr or '').strip()

        payload = self._parse_json_payload(stdout)

        is_error = bool(payload.get('is_error', False))
        success = completed.returncode == 0 and not is_error
        result_text = normalized_text(payload.get('result', ''))
        # ``payload`` is Claude CLI's terminal ``result`` event (wire
        # format) — Claude emits ``session_id``, kato normalizes
        # to ``AGENT_SESSION_ID`` downstream.
        session_id_value = fix_session_id(payload.get('session_id', ''))

        if completed.returncode != 0:
            detail = stderr or condensed_text(stdout) or 'no output'
            self.logger.error(
                'Claude CLI returned exit code %s for %s: %s',
                completed.returncode,
                log_label,
                detail,
            )
            raise RuntimeError(
                f'Claude CLI exited with status {completed.returncode}: {detail}'
            )
        if is_error:
            detail = result_text or stderr or 'unknown Claude CLI error'
            raise RuntimeError(f'Claude CLI reported an error: {detail}')

        # Output-side credential scan — closes residual #18 on the
        # detective side. The agent's response has already crossed to
        # Anthropic by the time we see it, so this cannot UNDO the
        # leak; it produces an auditable record so the operator knows
        # to rotate. Pattern names + redacted previews only — full
        # credential values are never logged.
        self._scan_response_for_credentials(result_text, log_label=log_label)

        result: dict[str, str | bool] = {
            ImplementationFields.SUCCESS: success,
            'summary': result_text,
        }
        if result_text:
            result[ImplementationFields.MESSAGE] = result_text
        if session_id_value:
            result[ImplementationFields.AGENT_SESSION_ID] = session_id_value
        return result

    def _scan_response_for_credentials(
        self,
        response_text: str,
        *,
        log_label: str,
    ) -> None:
        """Detective-side scan on the agent's response text.

        Delegates to the shared
        :func:`claude_core_lib...helpers.credential_scan.
        scan_text_for_credentials_and_phishing` so the one-shot and
        streaming paths produce identical audit signal. Two pattern
        families fire:

          * **Credential patterns** (residual #18) — pattern name +
            redacted preview only; the full credential value is never
            logged. Operators who see this should rotate the named
            credential. The agent's text has already crossed to
            Anthropic by the time the JSON payload returns, so this
            is an audit trail not a block.
          * **Phishing patterns** (residual #16, defense-in-depth) —
            agent output that looks like an attempt to trick the
            operator into running shell commands on their host
            (``curl|bash``, ``sudo`` snippets, ``eval $(curl …)``).
            Same audit-trail treatment.
        """
        scan_text_for_credentials_and_phishing(
            response_text,
            logger=self.logger,
            context_label=f'Claude response for {log_label}',
        )

    def _parse_json_payload(self, stdout: str) -> dict[str, object]:
        text = (stdout or '').strip()
        if not text:
            return {}

        # The CLI normally emits a single JSON object on stdout when called with
        # --output-format json. Fall back to scanning for the first balanced
        # JSON object so transient stdout chatter does not break parsing.
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = self._extract_first_json_object(text)
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    return item
        self.logger.warning(
            'failed to parse Claude CLI JSON output; got: %s',
            condensed_text(text)[:500],
        )
        return {}

    @staticmethod
    def _extract_first_json_object(text: str) -> object:
        brace_start = text.find('{')
        brace_end = text.rfind('}')
        if brace_start == -1 or brace_end <= brace_start:
            return {}
        try:
            return json.loads(text[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            return {}

    # ----- working directory resolution -----

    def _working_directories(
        self,
        prepared_task: Any | None,
    ) -> tuple[str, list[str]]:
        repositories = []
        if prepared_task is not None:
            repositories = list(prepared_task.repositories or [])
        repository_paths: list[str] = []
        for repository in repositories:
            local_path = normalized_text(text_from_attr(repository, 'local_path'))
            if local_path and local_path not in repository_paths:
                repository_paths.append(local_path)
        if not repository_paths:
            cwd = self._repository_root_path or os.getcwd()
            return cwd, []
        return repository_paths[0], repository_paths[1:]

    def _review_comment_cwd(self, comment: ReviewComment) -> str:
        repository_local_path = normalized_text(
            text_from_attr(comment, 'repository_local_path')
        )
        if repository_local_path:
            return repository_local_path
        if self._repository_root_path:
            return self._repository_root_path
        return os.getcwd()

    # ----- smoke test -----

    def _validate_model_smoke_test(self) -> None:
        if not self._model_smoke_test_enabled:
            return
        self._validate_model_access_smoke_test()

    def _validate_model_access_smoke_test(self) -> None:
        if self._model_access_smoke_test_ran:
            return
        self._run_model_access_validation()
        self._model_access_smoke_test_ran = True

    def _run_model_access_validation(self) -> None:
        self.logger.info('running Claude CLI model access validation')
        # Smoke test sends ``Reply with exactly: ok`` — no need for the
        # architecture doc / lessons here. Skipping them keeps the
        # boot command line short, which matters on Windows where
        # CreateProcess caps total args at ~32K chars.
        command = self._build_command(
            additional_dirs=[], agent_session_id='',
            include_system_prompt=False,
        )
        env = self._build_subprocess_env()
        # Boot-time validator: fixed ``SMOKE_TEST_PROMPT`` ("Reply with
        # exactly: ok"), no tools, no untrusted input. Sandbox-wrap is
        # intentionally skipped even when ``KATO_CLAUDE_DOCKER=true`` —
        # there is no workspace to leak from, the only egress is the
        # api.anthropic.com call that has to happen, and the operator
        # would pay container-spin cost on every startup with zero
        # security benefit.
        try:
            completed = subprocess.run(
                command,
                input=self.SMOKE_TEST_PROMPT,
                cwd=self._repository_root_path or None,
                env=env,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
                timeout=self.SMOKE_TEST_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f'Claude CLI smoke test did not finish within {self.SMOKE_TEST_TIMEOUT_SECONDS}s'
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or '').strip() or 'unknown error'
            raise RuntimeError(f'Claude CLI smoke test failed: {detail}')
        payload = self._parse_json_payload(completed.stdout or '')
        if payload.get('is_error'):
            detail = text_from_mapping(payload, 'result') or 'unknown Claude CLI error'
            raise RuntimeError(f'Claude CLI smoke test reported an error: {detail}')

    # ----- helpers -----

    def _host_binary(self) -> str:
        return self._binary_path or self._binary

    def _host_binary_argv(self) -> list[str]:
        """Argv prefix for invoking Claude on the host.

        On most platforms this is ``[claude_path]``. On Windows it
        may be ``[node.exe, cli.js]`` instead — the npm-installed
        ``claude.cmd`` is a cmd.exe shim, and cmd.exe caps command
        lines at ~8192 chars. Kato's ``--append-system-prompt``
        carries the entire architecture doc inline, which overflows
        that limit and raises ``[WinError 206] The filename or
        extension is too long``. Invoking ``node.exe`` directly with
        the underlying JS entry point sidesteps cmd.exe and bumps
        the limit to the CreateProcess maximum (~32K chars).

        Falls back to the resolved path on platforms / shim shapes
        we don't recognise — the caller's behaviour is unchanged
        when the override doesn't apply.
        """
        resolved = self._host_binary()
        via_node = self._resolve_windows_node_invocation(resolved)
        if via_node:
            return via_node
        return [resolved]

    @staticmethod
    def _resolve_windows_node_invocation(cmd_path: str) -> list[str] | None:
        """If ``cmd_path`` is a Windows npm cmd-shim, return
        ``[node.exe, script.js]`` to invoke directly. Returns None
        on non-Windows hosts, on non-shim binaries, or when we
        can't confidently parse the shim — caller falls back to
        invoking ``cmd_path`` as-is, which works for short command
        lines.
        """
        if os.name != 'nt':
            return None
        path = Path(cmd_path)
        if path.suffix.lower() not in ('.cmd', '.bat'):
            return None
        try:
            shim_text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return None
        # Standard npm shim references the JS entry point as a
        # quoted ``"...something.js"`` literal. Pull the first
        # match — the shim has fallback branches with the same
        # path, so the first one is enough.
        import re
        match = re.search(r'"([^"]+\.js)"', shim_text)
        if not match:
            return None
        js_ref = match.group(1)
        # npm shim uses ``%~dp0`` (the shim's own directory) as the
        # path prefix. Resolve it to the shim's parent directory
        # before checking the file exists.
        js_ref = js_ref.replace('%~dp0\\', '').replace('%~dp0/', '').replace('%~dp0', '')
        js_path = (path.parent / js_ref).resolve()
        if not js_path.is_file():
            return None
        # Prefer the ``node.exe`` next to the shim (npm / nvm layout)
        # so we use the same Node version the shim would have.
        node_path = path.parent / 'node.exe'
        if not node_path.is_file():
            node_via_path = shutil.which('node')
            if not node_via_path:
                return None
            node_path = Path(node_via_path)
        return [str(node_path), str(js_path)]

    @staticmethod
    def _coerce_max_turns(value: int | str | None) -> int | None:
        if value is None or value == '':
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        return parsed

    @classmethod
    def _coerce_effort(cls, value: str | None) -> str:
        """Validate the ``--effort`` value so we fail at startup, not mid-turn.

        Accepted: ``low``, ``medium``, ``high``, ``xhigh``, ``max``. Empty
        string means "don't pass --effort" (Claude uses its default).
        Anything else is rejected so a typo doesn't silently regress
        reasoning quality on production tasks.
        """
        normalized = normalized_text(value).lower()
        if not normalized:
            return ''
        if normalized not in cls.SUPPORTED_EFFORT_LEVELS:
            raise ValueError(
                f'invalid claude effort {value!r}; '
                f'expected one of {sorted(cls.SUPPORTED_EFFORT_LEVELS)} or empty'
            )
        return normalized


def _repository_local_paths(prepared_task) -> list[str]:
    """Pull the per-task workspace clone paths off ``prepared_task``.

    Used to render the ``workspace_scope_block`` at the top of every
    agent prompt — operator wants the agent to know exactly which
    paths it may touch and nothing else.
    """
    if prepared_task is None:
        return []
    repos = getattr(prepared_task, 'repositories', None) or []
    paths: list[str] = []
    for repo in repos:
        path = str(getattr(repo, 'local_path', '') or '').strip()
        if path:
            paths.append(path)
    return paths
