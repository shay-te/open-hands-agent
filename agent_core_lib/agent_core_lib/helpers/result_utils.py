from __future__ import annotations

from collections.abc import Mapping

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.helpers.text_utils import normalized_text, text_from_mapping

_TASK_SUMMARY_KEY = 'summary'
_TASK_BRANCH_NAME_KEY = 'branch_name'


def openhands_success_flag(
    payload: Mapping[object, object] | None,
    *,
    default: bool = False,
) -> bool:
    if not isinstance(payload, Mapping):
        return default
    if ImplementationFields.SUCCESS not in payload:
        return default
    value = payload.get(ImplementationFields.SUCCESS, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def openhands_session_id(payload: Mapping[object, object] | None) -> str:
    # ``payload`` may be either OpenHands' raw API response (wire
    # format — keys ``session_id`` / ``conversation_id``) or a payload
    # that has already been enriched with the host's internal
    # ``AGENT_SESSION_ID`` key. Check all three so the helper works
    # at both layers.
    for key in ('session_id', 'conversation_id', ImplementationFields.AGENT_SESSION_ID):
        value = text_from_mapping(payload, key)
        if value:
            return value
    return ''


def build_openhands_result(
    payload: Mapping[object, object] | None,
    *,
    branch_name: object = '',
    summary_fallback: object = '',
    default_commit_message: object | None = None,
    default_success: bool = False,
) -> dict[str, str | bool]:
    result: dict[str, str | bool] = {
        ImplementationFields.SUCCESS: openhands_success_flag(
            payload,
            default=default_success,
        ),
        _TASK_SUMMARY_KEY: text_from_mapping(payload, _TASK_SUMMARY_KEY, summary_fallback),
    }
    normalized_branch_name = normalized_text(branch_name)
    if normalized_branch_name:
        result[_TASK_BRANCH_NAME_KEY] = normalized_branch_name

    commit_message = text_from_mapping(payload, ImplementationFields.COMMIT_MESSAGE)
    if commit_message:
        result[ImplementationFields.COMMIT_MESSAGE] = commit_message
    elif default_commit_message is not None:
        result[ImplementationFields.COMMIT_MESSAGE] = normalized_text(default_commit_message)

    message = text_from_mapping(payload, ImplementationFields.MESSAGE)
    if message:
        result[ImplementationFields.MESSAGE] = message

    agent_session_id = openhands_session_id(payload)
    if agent_session_id:
        result[ImplementationFields.AGENT_SESSION_ID] = agent_session_id
    return result
