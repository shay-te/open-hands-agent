from openhands_agent.data_layers.data.fields import ImplementationFields, PullRequestFields
from openhands_agent.data_layers.data.task import Task
from openhands_agent.helpers.text_utils import text_from_mapping


def pull_request_repositories_text(pull_requests) -> str:
    if not isinstance(pull_requests, list):
        return '<none>'
    repository_ids = [
        text_from_mapping(pull_request, PullRequestFields.REPOSITORY_ID)
        for pull_request in pull_requests
        if isinstance(pull_request, dict)
    ]
    repository_ids = [repository_id for repository_id in repository_ids if repository_id]
    return ', '.join(repository_ids) if repository_ids else '<none>'


def pull_request_title(task: Task) -> str:
    task_id = str(task.id or '').strip()
    task_summary = str(task.summary or '').strip()
    if task_id and task_summary:
        return f'{task_id} {task_summary}'
    return task_id or task_summary or 'OpenHands task'


def pull_request_summary_comment(
    task: Task,
    pull_requests: list[dict[str, str]],
    failed_repositories: list[str],
    execution_report: str = '',
) -> str:
    lines = [f'OpenHands completed task {task.id}: {task.summary}.']
    if execution_report:
        lines.append('')
        lines.append('Execution report:')
        lines.append(execution_report)
    if pull_requests:
        lines.append('')
        lines.append('Published review links:')
        for pull_request in pull_requests:
            lines.append(
                f'- {pull_request[PullRequestFields.REPOSITORY_ID]}: '
                f'{pull_request[PullRequestFields.URL]}'
            )
    if failed_repositories:
        lines.append('')
        lines.append('Failed repositories: ' + ', '.join(failed_repositories))
    return '\n'.join(lines)


def pull_request_description(
    task: Task,
    execution: dict[str, str | bool],
) -> str:
    lines = [f'OpenHands completed task {task.id}: {task.summary}.']
    task_description = str(task.description or '').strip()
    if task_description:
        lines.append('')
        lines.append('Requested change:')
        lines.append(task_description)

    implementation_summary = str(execution.get(Task.summary.key, '') or '').strip()
    if implementation_summary:
        lines.append('')
        lines.append('Implementation summary:')
        lines.append(implementation_summary)

    execution_notes = str(execution.get(ImplementationFields.MESSAGE, '') or '').strip()
    if execution_notes:
        lines.append('')
        lines.append('Execution notes:')
        lines.append(execution_notes)

    return '\n'.join(lines)
