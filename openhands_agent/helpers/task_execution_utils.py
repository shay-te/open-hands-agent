from __future__ import annotations

from collections.abc import Mapping

from openhands_agent.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    StatusFields,
)
from openhands_agent.data_layers.data.task import Task


def implementation_succeeded(execution: Mapping[str, object]) -> bool:
    return bool(execution.get(ImplementationFields.SUCCESS, False))


def testing_succeeded(testing: Mapping[str, object]) -> bool:
    return bool(testing.get(ImplementationFields.SUCCESS, False))


def apply_testing_message(
    execution: dict[str, str | bool],
    testing: Mapping[str, object],
) -> dict[str, str | bool]:
    testing_message = str(testing.get(ImplementationFields.MESSAGE, '') or '').strip()
    if testing_message:
        execution = dict(execution)
        execution[ImplementationFields.MESSAGE] = testing_message
    return execution


def testing_failed_result(task_id: str) -> dict[str, object]:
    return {
        Task.id.key: task_id,
        StatusFields.STATUS: StatusFields.TESTING_FAILED,
        PullRequestFields.PULL_REQUESTS: [],
        PullRequestFields.FAILED_REPOSITORIES: [],
    }


def skip_task_result(
    task_id: str,
    pull_requests: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        Task.id.key: task_id,
        StatusFields.STATUS: StatusFields.SKIPPED,
        PullRequestFields.PULL_REQUESTS: pull_requests or [],
        PullRequestFields.FAILED_REPOSITORIES: [],
    }


def task_execution_report(execution: Mapping[str, object]) -> str:
    report_lines: list[str] = []
    implementation_summary = str(execution.get(Task.summary.key, '') or '').strip()
    if implementation_summary:
        report_lines.append('Implementation summary:')
        report_lines.append(implementation_summary)
    validation_report = str(execution.get(ImplementationFields.MESSAGE, '') or '').strip()
    if validation_report:
        report_lines.append('Validation report:')
        report_lines.append(validation_report)
    return '\n'.join(report_lines)
