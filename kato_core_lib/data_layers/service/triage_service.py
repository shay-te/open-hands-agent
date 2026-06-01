"""Tag-driven triage short-circuit for assigned tasks.

When a task carries the ``kato:triage:investigate`` tag, the
orchestrator skips implementation/testing/publishing and asks Claude
to spend one read-only turn classifying the issue. Claude's response
is parsed into one of the canonical outcome tags (priority or
disposition), kato writes that tag back onto the task, and the
investigate tag is removed so the next scan doesn't re-trigger
investigation.

The flow is deliberately minimal: read task description, ask Claude
"which of these tags fits?", apply the answer, comment with the
reasoning. No file changes, no PRs, no agent loop. Failed
investigations (Claude couldn't pick a tag, the agent backend isn't
configured, the ticket platform doesn't support tag mutation) leave
the task untouched and post an explanatory comment so the operator
can take over manually.
"""

from __future__ import annotations

import logging
import re

from kato_core_lib.data_layers.data.fields import (
    TRIAGE_OUTCOME_TAGS,
    StatusFields,
    TaskTags,
)
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.helpers.kato_tag_utils import build_triage_tag
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.mission_logging_utils import log_mission_step
from kato_core_lib.helpers.text_utils import normalized_text


# Match ``kato:triage:<level>`` anywhere in Claude's response. Both the
# prefix and the alternation are derived from the canonical tag constants
# so there is no second copy of the ``kato:triage:`` string or the outcome
# list to keep in sync. The match (group 1) is re-validated against
# TRIAGE_OUTCOME_TAGS below before being applied to the task.
_TRIAGE_OUTCOMES = tuple(
    tag[len(TaskTags.TRIAGE_PREFIX):] for tag in TRIAGE_OUTCOME_TAGS
)
_TRIAGE_OUTCOME_PATTERN = re.compile(
    re.escape(TaskTags.TRIAGE_PREFIX)
    + '(' + '|'.join(re.escape(outcome) for outcome in _TRIAGE_OUTCOMES) + ')',
    re.IGNORECASE,
)


# How the task summary maps onto the result envelope kato uses for
# every short-circuit handler (mirrors WaitPlanningService's shape).
TRIAGE_STATUS_TRIAGED = 'triaged'
TRIAGE_STATUS_INCONCLUSIVE = 'triage_inconclusive'
TRIAGE_STATUS_UNAVAILABLE = 'triage_unavailable'


class TriageService(object):
    """Owns the ``kato:triage:investigate`` short-circuit lifecycle."""

    def __init__(
        self,
        task_service,
        triage_investigator=None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        if task_service is None:
            raise ValueError('task_service is required')
        self._task_service = task_service
        # ``triage_investigator`` is a callable ``(task) -> str`` that
        # returns Claude's text response. Injected so unit tests can
        # short-circuit the model call. ``None`` means "no agent
        # backend wired" — kato refuses to triage and posts a comment.
        self._triage_investigator = triage_investigator
        self.logger = logger or configure_logger(self.__class__.__name__)

    def handle_task(self, task: Task) -> dict[str, object] | None:
        """If ``task`` is tagged for triage, run it and return the result.

        Returns None when the task has no triage tag (caller should
        continue with the normal task flow). Returns a result dict
        when triage was attempted (success, inconclusive, or
        unavailable) so the orchestrator can stop and move on.
        """
        if not _has_investigate_tag(task):
            return None
        self._log(task, 'starting triage investigation')
        if self._triage_investigator is None:
            return self._record_unavailable(task)
        try:
            response_text = self._triage_investigator(task)
        except Exception as exc:
            self.logger.exception(
                'triage investigator raised for task %s', task.id,
            )
            return self._record_inconclusive(
                task,
                reason=f'investigator failed: {exc}',
            )
        triage_tag = self._extract_triage_tag(response_text)
        if not triage_tag:
            return self._record_inconclusive(
                task,
                reason='Claude did not return a recognized kato:triage:<level> tag',
                claude_response=response_text,
            )
        return self._apply_triage(task, triage_tag, response_text)

    # ----- outcome handlers -----

    def _apply_triage(
        self,
        task: Task,
        triage_tag: str,
        claude_response: str,
    ) -> dict[str, object]:
        try:
            self._task_service.add_tag(task.id, triage_tag)
        except NotImplementedError as exc:
            return self._record_unavailable(task, reason=str(exc))
        except Exception as exc:
            self.logger.exception(
                'failed to add triage tag %s to task %s', triage_tag, task.id,
            )
            return self._record_inconclusive(
                task,
                reason=f'failed to add triage tag: {exc}',
                claude_response=claude_response,
            )
        # Best-effort: we want to remove the investigate tag now so
        # the next scan doesn't re-triage. Failure here is non-fatal
        # — the outcome tag is already on the task.
        try:
            self._task_service.remove_tag(task.id, TaskTags.TRIAGE_INVESTIGATE)
        except NotImplementedError:
            pass
        except Exception:
            self.logger.exception(
                'failed to remove %s from task %s; outcome tag was already added',
                TaskTags.TRIAGE_INVESTIGATE, task.id,
            )
        self._post_summary_comment(task, triage_tag, claude_response)
        self._log(task, 'triage applied: %s', triage_tag)
        return {
            Task.id.key: task.id,
            StatusFields.STATUS: TRIAGE_STATUS_TRIAGED,
            'triage_tag': triage_tag,
        }

    def _record_inconclusive(
        self,
        task: Task,
        *,
        reason: str = '',
        claude_response: str = '',
    ) -> dict[str, object]:
        self._log(task, 'triage inconclusive: %s', reason or 'unknown reason')
        comment_lines = [
            'Kato attempted triage but could not classify this task.',
        ]
        if reason:
            comment_lines.append(f'Reason: {reason}')
        if claude_response:
            comment_lines.append('')
            comment_lines.append('Investigator response:')
            comment_lines.append(claude_response.strip())
        self._safe_add_comment(task, '\n'.join(comment_lines))
        return {
            Task.id.key: task.id,
            StatusFields.STATUS: TRIAGE_STATUS_INCONCLUSIVE,
            'reason': reason,
        }

    def _record_unavailable(
        self,
        task: Task,
        *,
        reason: str = 'no triage investigator configured',
    ) -> dict[str, object]:
        self._log(task, 'triage unavailable: %s', reason)
        self._safe_add_comment(
            task,
            'Kato could not triage this task: '
            f'{reason}. Remove the kato:triage:investigate tag once '
            'the underlying issue is resolved.',
        )
        return {
            Task.id.key: task.id,
            StatusFields.STATUS: TRIAGE_STATUS_UNAVAILABLE,
            'reason': reason,
        }

    # ----- helpers -----

    def _post_summary_comment(
        self,
        task: Task,
        triage_tag: str,
        claude_response: str,
    ) -> None:
        body = (
            f'Kato triaged this task as `{triage_tag}`.\n\n'
            f'Investigator notes:\n{claude_response.strip()}'
        )
        self._safe_add_comment(task, body)

    def _safe_add_comment(self, task: Task, body: str) -> None:
        try:
            self._task_service.add_comment(task.id, body)
        except Exception:
            self.logger.exception(
                'failed to add triage comment to task %s', task.id,
            )

    @staticmethod
    def _extract_triage_tag(response_text: object) -> str:
        text = normalized_text(response_text)
        if not text:
            return ''
        match = _TRIAGE_OUTCOME_PATTERN.search(text)
        if match is None:
            return ''
        candidate = build_triage_tag(match.group(1).lower())
        # Defensive double-check against the canonical set: the regex
        # already constrains the alternatives but a future expansion
        # of TRIAGE_OUTCOME_TAGS without a regex update would be
        # caught here.
        return candidate if candidate in TRIAGE_OUTCOME_TAGS else ''

    def _log(self, task: Task, message: str, *args) -> None:
        log_mission_step(self.logger, str(task.id), message, *args)


def build_claude_triage_investigator(implementation_service):
    """Wrap a Claude implementation service's ``investigate`` method.

    Returns a ``(task) -> str`` callable, or ``None`` when the backing
    client doesn't expose ``investigate`` (currently only
    ``ClaudeCliClient`` does — OpenHands and other backends fall
    through to the "triage unavailable" path). The returned callable
    builds the triage prompt from the task and hands it to Claude.

    Lives in this module rather than ``kato_core_lib`` because the
    prompt template and the investigator wiring are part of the
    triage feature's contract; ``kato_core_lib`` should only compose
    dependencies, not own per-feature business logic.
    """
    backing_client = getattr(implementation_service, '_client', None)
    investigate = getattr(backing_client, 'investigate', None)
    if not callable(investigate):
        return None

    def investigator(task) -> str:
        return investigate(triage_prompt_for_task(task))

    return investigator


def triage_prompt_for_task(task: Task) -> str:
    """Compose the read-only classification prompt for one task.

    Surfaced as a module-level helper so the investigator builder
    above and any future caller (e.g. a dry-run CLI) can re-use it.
    """
    summary = str(getattr(task, 'summary', '') or '').strip()
    description = str(getattr(task, 'description', '') or '').strip()
    options = '\n'.join(f'- {tag}' for tag in TRIAGE_OUTCOME_TAGS)
    return (
        'You are triaging a backlog item. Pick exactly ONE of the '
        'following outcome tags and explain your reasoning in 1-3 sentences.\n\n'
        f'Available outcome tags:\n{options}\n\n'
        f'Task summary: {summary}\n\n'
        f'Task description:\n{description}\n\n'
        'Respond with the chosen tag on its own line at the end of your '
        'response, exactly in the form `kato:triage:<level>` (no other '
        'text on that line). Make no file changes; this is a read-only '
        'classification.'
    )


def _has_investigate_tag(task: Task) -> bool:
    raw_tags = getattr(task, 'tags', []) or []
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    target = TaskTags.TRIAGE_INVESTIGATE.lower()
    for raw_tag in raw_tags:
        if isinstance(raw_tag, dict):
            tag_text = normalized_text(raw_tag.get('name', ''))
        else:
            tag_text = normalized_text(getattr(raw_tag, 'name', raw_tag))
        if tag_text.lower() == target:
            return True
    return False
