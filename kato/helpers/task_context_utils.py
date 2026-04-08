from dataclasses import dataclass

from kato.data_layers.data.task import Task
from kato.data_layers.data.fields import ImplementationFields
from kato.helpers.text_utils import normalized_text, text_from_attr, text_from_mapping


@dataclass
class PreparedTaskContext:
    branch_name: str
    repositories: list[object]
    repository_branches: dict[str, str]


def task_has_actionable_definition(task: Task) -> bool:
    description = normalized_text(task.description)
    if description and description.lower() != 'no description provided.':
        return True
    summary = normalized_text(task.summary)
    return len(summary) >= 24 or len(summary.split()) >= 4


def repository_ids_text(repositories: list[object]) -> str:
    repository_ids = [
        text_from_attr(repository, 'id')
        for repository in repositories
        if text_from_attr(repository, 'id')
    ]
    return ', '.join(repository_ids) if repository_ids else '<none>'


def repository_destination_text(repositories: list[object]) -> str:
    entries = []
    for repository in repositories:
        repository_id = text_from_attr(repository, 'id')
        destination_branch = text_from_attr(repository, 'destination_branch')
        if not repository_id:
            continue
        entries.append(f'{repository_id}->{destination_branch or "default"}')
    return ', '.join(entries) if entries else '<none>'


def repository_branch_text(repository_branches: dict[str, str]) -> str:
    if not repository_branches:
        return '<none>'
    return ', '.join(
        f'{repository_id}->{branch_name}'
        for repository_id, branch_name in repository_branches.items()
    )


def session_suffix(payload: dict[str, str | bool]) -> str:
    session_id = text_from_mapping(payload, ImplementationFields.SESSION_ID)
    return f' (session {session_id})' if session_id else ''


def task_started_comment(task: Task, repositories: list[object] | None = None) -> str:
    repositories = repositories if repositories is not None else getattr(task, 'repositories', [])
    repositories = repositories or []
    repository_ids = [
        str(repository.id).strip()
        for repository in repositories
        if str(repository.id).strip()
    ]
    if not repository_ids:
        return 'Kato agent started working on this task.'
    if len(repository_ids) == 1:
        return (
            'Kato agent started working on this task in repository '
            f'{repository_ids[0]}.'
        )
    return (
        'Kato agent started working on this task in repositories: '
        f'{", ".join(repository_ids)}.'
    )
