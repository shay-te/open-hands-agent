"""Drive OpenAI's Codex CLI (``codex exec``) as a kato agent backend.

Same public surface as ``ClaudeCliClient`` so the orchestration
layer can use either backend interchangeably — selection is driven
by ``KATO_AGENT_BACKEND``. The Codex CLI flags differ from Claude
Code, but the method names + return shapes are identical so call
sites do not branch on backend.

This is a one-shot implementation (no streaming-session machinery
yet). Codex CLI's session-resume + stream-json input support can be
layered on later under the same ``session/`` folder name claude
uses, keeping the structural parity the operator asked for.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.helpers import agent_prompt_utils
from agent_core_lib.agent_core_lib.helpers.architecture_doc_utils import read_architecture_doc
from agent_core_lib.agent_core_lib.helpers.lessons_doc_utils import read_lessons_file
from agent_core_lib.agent_core_lib.helpers.logging_utils import configure_logger
from agent_core_lib.agent_core_lib.helpers.result_utils import build_openhands_result
from agent_core_lib.agent_core_lib.helpers.text_utils import (
    condensed_text,
    normalized_text,
    text_from_attr,
    text_from_mapping,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from sandbox_core_lib.sandbox_core_lib.workspace_delimiter import (
    wrap_untrusted_workspace_content,
)


class CodexCliClient(object):
    """Drive OpenAI's Codex CLI as the implementation/testing backend.

    The public methods (``validate_connection``, ``implement_task``,
    ``test_task``, ``investigate``, ``fix_review_comment``,
    ``fix_review_comments``, ``delete_conversation``,
    ``stop_all_conversations``, ``validate_model_access``) match
    ``ClaudeCliClient`` one-to-one so callers do not branch on
    backend. The only differences are inside the private
    ``_build_command`` / ``_parse_completed_process`` helpers where
    we translate to / from Codex's CLI shape.
    """

    DEFAULT_BINARY = 'codex'
    DEFAULT_TIMEOUT_SECONDS = 1800
    SAFE_PERMISSION_MODE = 'ask'
    BYPASS_PERMISSION_MODE = 'bypass'
    DEFAULT_ALLOWED_TOOLS = 'edit,write,read,shell'
    # Mirror of ``ClaudeCliClient.GIT_DENY_PATTERNS``. Kato is the
    # only component that runs git operations; Codex must NEVER
    # invoke git directly for the same reasons listed in the Claude
    # client. Patterns are codex-shaped (the exact denylist syntax
    # depends on Codex CLI version and can be tuned by an operator
    # via ``KATO_CODEX_DISALLOWED_TOOLS``).
    GIT_DENY_PATTERNS = ('shell(git:*)', 'shell(git *)')
    SMOKE_TEST_PROMPT = 'Reply with exactly: ok. Do not call any tools.'
    SMOKE_TEST_TIMEOUT_SECONDS = 120
    VERSION_PROBE_TIMEOUT_SECONDS = 30

    SUPPORTED_EFFORT_LEVELS = frozenset({'low', 'medium', 'high', 'xhigh', 'max'})

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
    ) -> None:
        self.max_retries = max(1, int(max_retries or 1))
        self._binary = normalized_text(binary) or self.DEFAULT_BINARY
        self._binary_path = ''
        self._model = normalized_text(model)
        self._max_turns = self._coerce_max_turns(max_turns)
        self._effort = self._coerce_effort(effort)
        self._bypass_permissions = bool(bypass_permissions)
        # Same semantics as the Claude client: docker mode wraps the
        # spawn in the hardened sandbox, bypass disables per-tool
        # prompts. The two are independent.
        self._docker_mode_on = bool(docker_mode_on)
        self._read_only_tools_on = bool(read_only_tools_on)
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
        self.logger = configure_logger(self.__class__.__name__)
        if self._bypass_permissions:
            self.logger.warning(
                'KATO_CODEX_BYPASS_PERMISSIONS=true: Codex will run with '
                'permission prompts disabled. Per-tool prompts are off — '
                'the agent can run shell, edit, write, and any other tool '
                'without asking. The operator who set this flag accepts '
                'responsibility for any harm caused by the agent. '
                'See SECURITY.md.'
            )

    @property
    def _permission_mode(self) -> str:
        return (
            self.BYPASS_PERMISSION_MODE
            if self._bypass_permissions
            else self.SAFE_PERMISSION_MODE
        )

    # ----- public API parity with ClaudeCliClient / KatoClient -----

    @staticmethod
    def _running_inside_docker() -> bool:
        # /.dockerenv is the canonical marker the Docker engine creates
        # inside every container. Mirrors ``ClaudeCliClient`` so the two
        # backends share the same "is this Docker?" definition.
        return Path('/.dockerenv').exists()

    def validate_connection(self) -> None:
        if self._running_inside_docker():
            raise RuntimeError(
                'KATO_AGENT_BACKEND=codex is not supported inside Docker. '
                'The Codex CLI authenticates against your host credentials, '
                'and the container cannot reach those. Run kato locally '
                'instead. If you genuinely need Docker, switch to '
                'KATO_AGENT_BACKEND=openhands.'
            )
        binary_path = shutil.which(self._binary)
        if not binary_path:
            raise RuntimeError(
                f'\n'
                f'Codex CLI ("{self._binary}") was not found on PATH.\n'
                f'\n'
                f'Install Codex CLI (works on macOS, Linux, and Windows):\n'
                f'\n'
                f'    npm install -g @openai/codex\n'
                f'\n'
                f'Prerequisite: Node.js 18+ (https://nodejs.org/). Verify with:\n'
                f'\n'
                f'    node --version\n'
                f'    codex --version\n'
                f'\n'
                f'After install, the ``codex`` binary must be on PATH (npm puts it\n'
                f'there automatically). If you installed it somewhere else, set\n'
                f'KATO_CODEX_BINARY to the full path.\n'
            )
        self._binary_path = binary_path
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
                f'Codex CLI binary "{self._binary}" failed to launch: {exc}'
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip() or 'unknown error'
            raise RuntimeError(
                f'Codex CLI binary "{self._binary}" failed to report a version: {detail}'
            )
        self.logger.info(
            'Codex CLI is available at %s (%s)',
            binary_path,
            condensed_text(result.stdout),
        )
        self._validate_model_smoke_test()

    def validate_model_access(self) -> None:
        self._validate_model_access_smoke_test()

    def delete_conversation(self, conversation_id: str) -> None:
        # Codex CLI sessions are local on disk; nothing to clean up remotely.
        # Matches the Claude backend's no-op contract.
        return

    def stop_all_conversations(self) -> None:
        # No remote agent-server containers exist for the Codex CLI backend.
        return

    def implement_task(
        self,
        task: Any,
        session_id: str = '',
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
            session_id=session_id,
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
        """Read-only single turn — used by the triage flow."""
        normalized_prompt = normalized_text(prompt)
        if not normalized_prompt:
            raise ValueError('prompt is required to run an investigation')
        normalized_cwd = normalized_text(cwd)
        if not normalized_cwd:
            normalized_cwd = self._repository_root_path or os.getcwd()
        original_disallowed = self._disallowed_tools
        original_allowed = self._allowed_tools
        try:
            self._disallowed_tools = 'edit,write,shell'
            self._allowed_tools = 'read'
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
        session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
    ) -> dict[str, str | bool]:
        return self.fix_review_comments(
            [comment],
            branch_name,
            session_id=session_id,
            task_id=task_id,
            task_summary=task_summary,
        )

    def fix_review_comments(
        self,
        comments: list[ReviewComment],
        branch_name: str,
        session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
        mode: str = 'fix',
    ) -> dict[str, str | bool]:
        """Address one or more PR review comments in a single spawn.

        Mirrors ``ClaudeCliClient.fix_review_comments`` — same signature,
        same return contract, same ``mode`` semantics (``fix`` vs
        ``answer``).
        """
        if not comments:
            raise ValueError('fix_review_comments requires at least one comment')
        cwd = self._review_comment_cwd(comments[0])
        if len(comments) == 1:
            single = comments[0]
            prompt = self._build_review_prompt(
                single, branch_name, workspace_path=cwd, mode=mode,
            )
        else:
            prompt = self._build_review_comments_batch_prompt(
                comments, branch_name, workspace_path=cwd, mode=mode,
            )
        result = self._run_prompt_result(
            prompt=prompt,
            cwd=cwd,
            additional_dirs=[],
            session_id=session_id,
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

    # ----- prompt builders -----

    def _build_implementation_prompt(
        self,
        task: Any,
        prepared_task: Any | None = None,
    ) -> str:
        scope_block = agent_prompt_utils.workspace_scope_block(
            _repository_local_paths(prepared_task),
        )
        repository_scope = agent_prompt_utils.repository_scope_text(task, prepared_task)
        agents_instructions = agent_prompt_utils.agents_instructions_text(prepared_task)
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
    ) -> str:
        first = comments[0]
        repository_context = agent_prompt_utils.review_repository_context(first)
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
        )
        scope_prefix = f'{scope_block}\n' if scope_block else ''
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
                '- Do NOT modify any files. Do not call edit, write, or any '
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
    ) -> str:
        repository_context = agent_prompt_utils.review_repository_context(comment)
        review_context = agent_prompt_utils.review_comment_context_text(comment)
        location_text = agent_prompt_utils.review_comment_location_text(comment)
        snippet_text = (
            agent_prompt_utils.review_comment_code_snippet(comment, workspace_path)
            if workspace_path
            else ''
        )
        untrusted_comment_body = wrap_untrusted_workspace_content(
            comment.body,
            source_path=f'pr-comment:{comment.author}',
        )
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
                '- Do NOT modify any files. Do not call edit, write, or any '
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
            '- Use edit/write/read for file edits and reads.\n'
            '- Use shell sparingly and only for non-destructive needs (rg, sed -n, cat, ls).\n'
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
        session_id: str = '',
        log_label: str = '',
        task_id: str = '',
    ) -> dict[str, str | bool]:
        payload = self._run_prompt(
            prompt=prompt,
            cwd=cwd,
            additional_dirs=additional_dirs,
            session_id=session_id,
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
        session_id: str = '',
        log_label: str = '',
        task_id: str = '',
    ) -> dict[str, str | bool]:
        command = self._build_command(
            additional_dirs=additional_dirs,
            session_id=session_id,
            resolve_binary=not self._docker_mode_on,
        )
        env = self._build_subprocess_env()
        log_label = log_label or 'Codex CLI'
        spawn_cwd: str | None = cwd or None
        if self._docker_mode_on:
            from sandbox_core_lib.sandbox_core_lib.manager import (
                SandboxError,
                check_spawn_rate,
                ensure_image,
                enforce_no_workspace_secrets,
                make_container_name,
                record_spawn,
                wrap_command,
            )
            workspace_path = cwd or self._repository_root_path or os.getcwd()
            try:
                ensure_image(logger=self.logger)
            except SandboxError as exc:
                raise RuntimeError(
                    f'failed to prepare Codex sandbox image: {exc}',
                ) from exc
            try:
                check_spawn_rate()
            except SandboxError as exc:
                raise RuntimeError(
                    f'sandbox spawn rate-limited: {exc}',
                ) from exc
            container_name = make_container_name(task_id)
            try:
                enforce_no_workspace_secrets(workspace_path, logger=self.logger)
            except SandboxError as exc:
                raise RuntimeError(
                    f'sandbox spawn blocked: {exc}',
                ) from exc
            command = wrap_command(
                command,
                workspace_path=workspace_path,
                container_name=container_name,
                task_id=task_id or 'unknown',
            )
            try:
                record_spawn(
                    task_id=task_id or 'unknown',
                    container_name=container_name,
                    workspace_path=workspace_path,
                    logger=self.logger,
                )
            except SandboxError as exc:
                raise RuntimeError(
                    f'sandbox audit log required but failed: {exc}',
                ) from exc
            spawn_cwd = None
        self.logger.info('Mission %s: invoking Codex CLI', log_label)
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
                f'Codex CLI did not finish within {self._timeout_seconds}s for {log_label}'
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f'failed to invoke Codex CLI binary "{self._binary}": {exc}'
            ) from exc

        return self._parse_completed_process(completed, log_label=log_label)

    def _build_command(
        self,
        *,
        additional_dirs: list[str],
        session_id: str,
        resolve_binary: bool = True,
        include_system_prompt: bool = True,
    ) -> list[str]:
        # Codex CLI uses ``codex exec`` for non-interactive runs. Flag
        # names differ from Claude Code; the structure (prompt on
        # stdin, JSON result on stdout, system-prompt-append for the
        # architecture doc + lessons) matches.
        command: list[str] = [
            *(self._host_binary_argv() if resolve_binary else [self._binary]),
            'exec',
            '--json',
            '--ask-for-approval',
            self._permission_mode,
        ]
        if self._model:
            command.extend(['--model', self._model])
        if self._max_turns is not None:
            command.extend(['--max-turns', str(self._max_turns)])
        if self._effort:
            command.extend(['--reasoning-effort', self._effort])
        merged_allowed = self._merge_allowed_with_read_only_allowlist(self._allowed_tools)
        if merged_allowed:
            command.extend(['--allow-tools', merged_allowed])
        merged_disallowed = self._merge_disallowed_with_git_deny(self._disallowed_tools)
        command.extend(['--deny-tools', merged_disallowed])
        if include_system_prompt:
            architecture_doc = read_architecture_doc(
                self._architecture_doc_path, logger=self.logger,
            )
            lessons_text = read_lessons_file(
                self._lessons_path, logger=self.logger,
            )
            from sandbox_core_lib.sandbox_core_lib.system_prompt import compose_system_prompt
            appended_system_prompt = compose_system_prompt(
                architecture_doc,
                docker_mode_on=self._docker_mode_on,
                lessons=lessons_text,
            )
            if appended_system_prompt:
                command.extend(['--system-prompt-append', appended_system_prompt])
        normalized_session_id = normalized_text(session_id)
        if normalized_session_id:
            command.extend(['--resume', normalized_session_id])
        for directory in additional_dirs:
            normalized_dir = normalized_text(directory)
            if normalized_dir:
                command.extend(['--workspace', normalized_dir])
        command.extend(self._extra_args)
        return command

    def _merge_allowed_with_read_only_allowlist(self, operator_allowed: str) -> str:
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
        for pattern in sorted(READ_ONLY_TOOLS_ALLOWLIST):
            if pattern not in seen:
                existing.append(pattern)
                seen[pattern] = True
        return ','.join(existing)

    @classmethod
    def _merge_disallowed_with_git_deny(cls, operator_disallowed: str) -> str:
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
        env = os.environ.copy()
        # Force JSON output to stdout and prevent any TTY-dependent behavior.
        env.setdefault('CODEX_NONINTERACTIVE', '1')
        return env

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
        session_id_value = normalized_text(payload.get('session_id', ''))

        if completed.returncode != 0:
            detail = stderr or condensed_text(stdout) or 'no output'
            self.logger.error(
                'Codex CLI returned exit code %s for %s: %s',
                completed.returncode,
                log_label,
                detail,
            )
            raise RuntimeError(
                f'Codex CLI exited with status {completed.returncode}: {detail}'
            )
        if is_error:
            detail = result_text or stderr or 'unknown Codex CLI error'
            raise RuntimeError(f'Codex CLI reported an error: {detail}')

        self._scan_response_for_credentials(result_text, log_label=log_label)

        result: dict[str, str | bool] = {
            ImplementationFields.SUCCESS: success,
            'summary': result_text,
        }
        if result_text:
            result[ImplementationFields.MESSAGE] = result_text
        if session_id_value:
            result[ImplementationFields.SESSION_ID] = session_id_value
        return result

    def _scan_response_for_credentials(
        self,
        response_text: str,
        *,
        log_label: str,
    ) -> None:
        from sandbox_core_lib.sandbox_core_lib.credential_patterns import (
            find_credential_patterns,
            find_phishing_patterns,
            summarize_findings,
        )

        if not response_text:
            return
        cred_findings = find_credential_patterns(response_text)
        if cred_findings:
            self.logger.warning(
                'CREDENTIAL PATTERN DETECTED in Codex response for %s: %s. '
                'The agent response has already been transmitted to OpenAI; '
                'rotate the named credential(s) immediately.',
                log_label,
                summarize_findings(cred_findings),
            )
        phishing_findings = find_phishing_patterns(response_text)
        if phishing_findings:
            self.logger.warning(
                'PHISHING PATTERN DETECTED in Codex response for %s: %s. '
                'The agent appears to be instructing the operator to run '
                'shell commands on their host. Treat as untrusted.',
                log_label,
                summarize_findings(phishing_findings),
            )

    def _parse_json_payload(self, stdout: str) -> dict[str, object]:
        text = (stdout or '').strip()
        if not text:
            return {}

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
            'failed to parse Codex CLI JSON output; got: %s',
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
        self.logger.info('running Codex CLI model access validation')
        command = self._build_command(
            additional_dirs=[], session_id='',
            include_system_prompt=False,
        )
        env = self._build_subprocess_env()
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
                f'Codex CLI smoke test did not finish within {self.SMOKE_TEST_TIMEOUT_SECONDS}s'
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or '').strip() or 'unknown error'
            raise RuntimeError(f'Codex CLI smoke test failed: {detail}')
        payload = self._parse_json_payload(completed.stdout or '')
        if payload.get('is_error'):
            detail = text_from_mapping(payload, 'result') or 'unknown Codex CLI error'
            raise RuntimeError(f'Codex CLI smoke test reported an error: {detail}')

    # ----- helpers -----

    def _host_binary(self) -> str:
        return self._binary_path or self._binary

    def _host_binary_argv(self) -> list[str]:
        """Argv prefix for invoking Codex on the host.

        Mirrors :meth:`ClaudeCliClient._host_binary_argv`. On Windows
        the npm-installed ``codex.cmd`` is a cmd.exe shim with the
        same 8K command-line cap as Claude's shim, so the same
        ``node.exe + script.js`` workaround applies. Falls back to
        the resolved path on platforms / shim shapes we don't
        recognise.
        """
        resolved = self._host_binary()
        via_node = self._resolve_windows_node_invocation(resolved)
        if via_node:
            return via_node
        return [resolved]

    @staticmethod
    def _resolve_windows_node_invocation(cmd_path: str) -> list[str] | None:
        if os.name != 'nt':
            return None
        path = Path(cmd_path)
        if path.suffix.lower() not in ('.cmd', '.bat'):
            return None
        try:
            shim_text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return None
        import re
        match = re.search(r'"([^"]+\.js)"', shim_text)
        if not match:
            return None
        js_ref = match.group(1)
        js_ref = js_ref.replace('%~dp0\\', '').replace('%~dp0/', '').replace('%~dp0', '')
        js_path = (path.parent / js_ref).resolve()
        if not js_path.is_file():
            return None
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
        normalized = normalized_text(value).lower()
        if not normalized:
            return ''
        if normalized not in cls.SUPPORTED_EFFORT_LEVELS:
            raise ValueError(
                f'invalid codex effort {value!r}; '
                f'expected one of {sorted(cls.SUPPORTED_EFFORT_LEVELS)} or empty'
            )
        return normalized


def _repository_local_paths(prepared_task) -> list[str]:
    """Pull the per-task workspace clone paths off ``prepared_task``."""
    if prepared_task is None:
        return []
    repos = getattr(prepared_task, 'repositories', None) or []
    paths: list[str] = []
    for repo in repos:
        path = str(getattr(repo, 'local_path', '') or '').strip()
        if path:
            paths.append(path)
    return paths
