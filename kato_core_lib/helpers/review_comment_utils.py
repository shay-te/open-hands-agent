from __future__ import annotations
from agent_core_lib.agent_core_lib.helpers.text_utils import text_from_mapping
from agent_core_lib.agent_core_lib.helpers.session_id_utils import fix_session_id

import re
from dataclasses import dataclass

from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
    StatusFields,
    TaskFields,
)
from kato_core_lib.helpers.task_execution_utils import task_execution_report
from kato_core_lib.helpers.text_utils import normalized_text, text_from_mapping

KATO_REVIEW_COMMENT_FIXED_PREFIX = 'Kato addressed review comment '
KATO_REVIEW_COMMENT_REPLY_PREFIX = 'Kato addressed this review comment'


class ReviewReplyTemplate:
    """Template fragments for the auto-reply kato posts on a review
    comment. Grouped here so the visible header, separator, and the
    "did nothing — here is why" fallbacks all live next to each
    other instead of bleeding through the file as loose module-level
    constants.

    Why these are templates (not f-strings inline in the helper):
    the strings render in Bitbucket / GitHub markdown, so any change
    to tags or whitespace has to be reviewed in one place; and the
    ``HEADER`` substring is what ``is_kato_review_comment_reply``
    looks for on poll, so we can never accidentally drift the wire
    format from the dedupe rule.
    """

    # Visible (smaller) header line. ``<sub>`` is used (not
    # ``<small>``) because Bitbucket Cloud strips ``<small>``
    # whereas it preserves ``<sub>`` and renders it as smaller
    # text — same on GitHub. Must contain the reply prefix verbatim
    # so the dedupe check below still recognises kato's own replies.
    HEADER = (
        '<sub>Kato addressed this review comment and pushed a '
        'follow-up update.</sub>'
    )

    # Header for answer-mode replies: no code was changed, nothing
    # was pushed. Placed at the very top so it cannot be missed even
    # if the agent's answer text mistakenly claims otherwise.
    ANSWER_HEADER = (
        '<sub>**No code was changed and nothing was pushed.** '
        'Kato read this as a question and answered it below — no fix was applied. '
        'If you expected a code change, re-open the thread and re-phrase as an '
        'imperative (e.g. *"Fix this to handle the null case."*).</sub>'
    )

    # Separator between the auto-header and the per-comment
    # summary. The reviewer can see at a glance: header above the
    # rule is boilerplate, content below is what kato actually did.
    SEPARATOR = '\n\n---\n\n'

    # Final fallback when the agent finished, kato resolved the
    # thread, BUT no summary, message, result, or error string was
    # captured. Renders as italic so it is visibly distinct from a
    # real summary — the reviewer needs to know "kato made no
    # observable change" rather than reading the boilerplate header
    # and assuming work happened.
    EMPTY_SUMMARY = (
        '_Kato did nothing observable on this comment — the '
        'pipeline reported success but produced no implementation '
        'summary, no validation report, and no agent output. '
        'Check the planning UI for the full transcript; if this '
        'looks wrong, re-open the thread to retry._'
    )

    # Used when we DO have a hint of why nothing happened (e.g., a
    # short ``message`` or ``result`` field from the execution
    # dict). Embeds the hint verbatim so the reviewer sees kato's
    # own words rather than a generic "did nothing" stub.
    DID_NOTHING_PREFIX = (
        '_Kato did not commit any changes for this comment._\n\n'
        '**Reason:** '
    )


@dataclass(frozen=True)
class ReviewFixContext(object):
    repository_id: str
    branch_name: str
    session_id: str
    task_id: str
    task_summary: str
    pull_request_title: str


def review_comment_from_payload(payload: dict) -> ReviewComment:
    try:
        comment = ReviewComment(
            pull_request_id=str(payload[ReviewCommentFields.PULL_REQUEST_ID]),
            comment_id=str(payload[ReviewCommentFields.COMMENT_ID]),
            author=str(payload[ReviewCommentFields.AUTHOR]),
            body=str(payload[ReviewCommentFields.BODY]),
            file_path=str(payload.get(ReviewCommentFields.FILE_PATH, '') or ''),
            line_number=_coerce_optional_int(
                payload.get(ReviewCommentFields.LINE_NUMBER, ''),
            ),
            line_type=str(payload.get(ReviewCommentFields.LINE_TYPE, '') or ''),
            commit_sha=str(payload.get(ReviewCommentFields.COMMIT_SHA, '') or ''),
        )
        if PullRequestFields.REPOSITORY_ID in payload:
            setattr(
                comment,
                PullRequestFields.REPOSITORY_ID,
                str(payload[PullRequestFields.REPOSITORY_ID]),
            )
        setattr(
            comment,
            ReviewCommentFields.ALL_COMMENTS,
            normalize_comment_context(payload.get(ReviewCommentFields.ALL_COMMENTS, [])),
        )
        return comment
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'invalid review comment payload: {exc}') from exc


def _coerce_optional_int(value: object) -> int | str:
    if value is None or value == '':
        return ''
    try:
        n = int(value)
    except (TypeError, ValueError):
        return ''
    if n <= 0:
        return ''
    return n


def comment_context_entry(comment: ReviewComment) -> dict[str, str]:
    return {
        ReviewCommentFields.COMMENT_ID: str(comment.comment_id),
        ReviewCommentFields.AUTHOR: str(comment.author),
        ReviewCommentFields.BODY: str(comment.body),
    }


def review_comment_resolution_key(comment: ReviewComment) -> tuple[str, str]:
    resolution_target_type = str(
        getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE, '') or 'comment'
    ).strip() or 'comment'
    resolution_target_id = str(
        getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, '')
        or comment.comment_id
        or ''
    ).strip()
    return resolution_target_type, resolution_target_id


def review_comment_processing_keys(comment: ReviewComment) -> set[str]:
    keys = {normalized_text(comment.comment_id)}
    resolution_target_type, resolution_target_id = review_comment_resolution_key(comment)
    if resolution_target_id:
        keys.add(f'{resolution_target_type}:{resolution_target_id}')
    return {key for key in keys if key}


def is_kato_review_comment_reply(comment: ReviewComment) -> bool:
    """True when ``comment`` is one of kato's own auto-replies.

    Drops a leading ``<sub>``/``<small>`` opening tag before the
    prefix check so the reply still de-dupes after the visible
    header was wrapped in ``<sub>...</sub>`` to render smaller.
    Existing un-wrapped historical replies still match the bare
    prefix, so old PRs do not regress.
    """
    body = normalized_text(comment.body)
    for opener in ('<sub>', '<small>'):
        if body.startswith(opener):
            body = body[len(opener):]
            break
    return body.startswith(
        (
            KATO_REVIEW_COMMENT_FIXED_PREFIX,
            KATO_REVIEW_COMMENT_REPLY_PREFIX,
        )
    )


# Match @mention: @ not preceded by a dot or word character (so email
# addresses like user@host are excluded) followed by at least one word char.
_MENTION_RE = re.compile(r'(?<![.\w])@\w')


def is_mention_comment(comment: ReviewComment) -> bool:
    """True when the comment body contains an @mention.

    @mentions direct a comment at a specific person, not kato, so kato
    skips these entirely rather than treating them as fix or question requests.
    Email-style addresses (user@host) are not counted as mentions.
    """
    body = str(getattr(comment, 'body', '') or '')
    return bool(_MENTION_RE.search(body))


# Heuristic: question vs fix-request classification for review comments.
#
# Why this exists: review comments like "how will this work when X?"
# are questions, not fix requests. Today kato treats every comment as
# a fix request — pushes a "follow-up update" reply even when nothing
# changed. ``is_question_comment`` lets the service route pure-question
# batches through an answer-only flow: agent reads the code, replies
# with a plain-text answer, no commit, no push.
#
# Conservative by design: false-positive cost is high (a fix request
# misclassified as a question gets *no* fix, just a chat reply), so
# the rule defaults to "fix" on anything ambiguous. The reviewer's
# wording has to look unambiguously like a question for the answer
# flow to fire.

# Question must end with ``?`` and start with one of these words.
_QUESTION_START_WORDS = (
    'how', 'why', 'what', 'when', 'where', 'who', 'which',
    'could', 'can', 'will', 'would', 'should', 'is', 'are', 'do',
    'does', 'did', 'have', 'has', 'any reason', 'curious',
)
# Imperative-leaning words that disqualify even a ?-ending comment
# from the answer flow. Catches phrasing like "should this be a
# constant?" / "shouldn't we use X?" — those read as fix requests
# despite the question mark.
_FIX_REQUEST_WORDS = (
    'fix', 'rename', 'extract', 'remove', 'delete', 'add',
    'use a constant', 'use the constant',
    'should be', 'should use', 'shouldn\'t this', 'should this',
    'shouldn\'t we', 'should we',
    'needs to', 'need to',
    'change this', 'move this', 'replace this',
    'make this', 'make it',
)
# Cap on body length. Long comments are rarely pure questions; the
# reviewer usually buries a fix request inside the explanation.
_QUESTION_MAX_LENGTH = 400


def is_question_comment(comment: ReviewComment) -> bool:
    """True when ``comment.body`` looks unambiguously like a question.

    Conservative — every check has to pass. Returns False on any
    ambiguity so kato defaults to fix-mode (today's behaviour) for
    anything the heuristic can't confidently classify as a question.
    """
    body = str(getattr(comment, 'body', '') or '').strip()
    if not body:
        return False
    if not body.endswith('?'):
        return False
    if len(body) > _QUESTION_MAX_LENGTH:
        return False
    lowered = body.lower()
    if not lowered.startswith(_QUESTION_START_WORDS):
        return False
    if any(token in lowered for token in _FIX_REQUEST_WORDS):
        return False
    return True


def is_question_only_batch(comments) -> bool:
    """True when every comment in ``comments`` looks like a question.

    Used by the service to decide between fix-mode and answer-mode
    for the whole batch. Mixed batches stay on fix-mode — splitting
    the batch into two agent spawns would erase the batching
    efficiency for marginal benefit.
    """
    comments = list(comments or [])
    if not comments:
        return False
    return all(is_question_comment(c) for c in comments)


def review_comment_fixed_comment(comment: ReviewComment) -> str:
    return (
        f'{KATO_REVIEW_COMMENT_FIXED_PREFIX}{comment.comment_id} '
        f'on pull request {comment.pull_request_id}.'
    )


def review_comment_reply_body(execution: dict[str, str | bool]) -> str:
    """Build the visible reply body for a successfully-addressed
    review comment.

    Layout: [smaller boilerplate header] --- [what kato actually
    did]. The separator is mandatory: previously, when the agent
    produced no summary text, the reply body was just the
    boilerplate and reviewers had no signal whether code changed.
    Now the body always carries either a real summary or an
    explicit "did nothing — here is why" line beneath the rule.
    """
    report = task_execution_report(execution).strip()
    summary = report or _did_nothing_summary(execution)
    return (
        f'{ReviewReplyTemplate.HEADER}'
        f'{ReviewReplyTemplate.SEPARATOR}'
        f'{summary}'
    )


def _did_nothing_summary(execution: dict[str, str | bool]) -> str:
    """Surface whatever signal the execution dict has about why
    kato produced no observable change.

    Priority order:
    1. ``error`` — pipeline raised; surface it verbatim.
    2. ``result`` / ``message`` — the agent's own final text
       (Claude one-shot stores it in ``result``; OpenHands /
       streaming runs put it in ``message``). Truncated so a
       multi-page transcript doesn't drown the comment thread.
    3. ``success=False`` with no message — explicit "pipeline
       failed" stub; the operator can dig into the planning UI.
    4. Nothing usable — the empty-summary template.

    The point is to never post the boilerplate header alone:
    reviewers have repeatedly read the header as "kato did
    something" and merged without checking, which is a real bug.
    """
    error = text_from_mapping(execution, 'error')
    if error:
        return f'{ReviewReplyTemplate.DID_NOTHING_PREFIX}{_truncate(error)}'
    for key in ('result', ImplementationFields.MESSAGE, 'summary'):
        hint = str(execution.get(key) or '').strip()
        if hint:
            return f'{ReviewReplyTemplate.DID_NOTHING_PREFIX}{_truncate(hint)}'
    success = bool(execution.get(ImplementationFields.SUCCESS, True))
    if not success:
        return (
            f'{ReviewReplyTemplate.DID_NOTHING_PREFIX}'
            'pipeline reported failure but produced no error message.'
        )
    return ReviewReplyTemplate.EMPTY_SUMMARY


# Cap the embedded reason at one paragraph: long agent transcripts
# in a comment thread are unreadable, and Bitbucket / GitHub both
# truncate very long comments anyway.
_DID_NOTHING_REASON_MAX_CHARS = 600


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) <= _DID_NOTHING_REASON_MAX_CHARS:
        return text
    return text[:_DID_NOTHING_REASON_MAX_CHARS].rstrip() + '…'


def review_comment_answer_body(execution: dict[str, str | bool]) -> str:
    """Build the reply body for an answer-mode review comment.

    Always prefixes the ANSWER_HEADER so the reviewer cannot mistake
    this for a push-backed fix, even if the agent's output text
    happens to say "I've pushed a change" (an LLM hallucination that
    can occur when the model doesn't track whether it's in answer vs
    fix mode).

    Different backends populate different fields:
    - Claude one-shot puts the final text in ``result``.
    - OpenHands / Claude streaming put it in ``message``.
    - Some implementations put it in ``summary``.
    Try them in priority order; first non-empty wins.
    """
    for key in ('message', 'result', ImplementationFields.MESSAGE, 'summary'):
        value = str(execution.get(key) or '').strip()
        if value:
            answer = value
            break
    else:
        answer = (
            'Kato read this question but did not produce an answer. '
            'Re-open the thread for a fresh attempt.'
        )
    return (
        f'{ReviewReplyTemplate.ANSWER_HEADER}'
        f'{ReviewReplyTemplate.SEPARATOR}'
        f'{answer}'
    )


def normalize_comment_context(all_comments) -> list[dict[str, str]]:
    if not isinstance(all_comments, list):
        return []

    normalized_comments: list[dict[str, str]] = []
    for item in all_comments:
        if isinstance(item, ReviewComment):
            normalized_comments.append(
                {
                    ReviewCommentFields.COMMENT_ID: str(item.comment_id),
                    ReviewCommentFields.AUTHOR: str(item.author),
                    ReviewCommentFields.BODY: str(item.body),
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        normalized_comment = {
            ReviewCommentFields.COMMENT_ID: str(item.get(ReviewCommentFields.COMMENT_ID, '')),
            ReviewCommentFields.AUTHOR: str(item.get(ReviewCommentFields.AUTHOR, '')),
            ReviewCommentFields.BODY: str(item.get(ReviewCommentFields.BODY, '')),
        }
        if not any(normalized_comment.values()):
            continue
        normalized_comments.append(normalized_comment)
    return normalized_comments


def review_fix_context_from_mapping(context: dict[str, str]) -> ReviewFixContext:
    return ReviewFixContext(
        repository_id=text_from_mapping(context, PullRequestFields.REPOSITORY_ID),
        branch_name=text_from_mapping(context, Task.branch_name.key),
        session_id=fix_session_id(context.get(ImplementationFields.AGENT_SESSION_ID)),
        task_id=text_from_mapping(context, TaskFields.ID),
        task_summary=text_from_mapping(context, TaskFields.SUMMARY),
        pull_request_title=text_from_mapping(context, PullRequestFields.TITLE),
    )


def review_fix_result(
    comment: ReviewComment,
    review_context: ReviewFixContext,
) -> dict[str, str]:
    return {
        StatusFields.STATUS: StatusFields.UPDATED,
        ReviewCommentFields.PULL_REQUEST_ID: comment.pull_request_id,
        Task.branch_name.key: review_context.branch_name,
        PullRequestFields.REPOSITORY_ID: review_context.repository_id,
    }
