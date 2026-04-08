from __future__ import annotations

from typing import TYPE_CHECKING

from kato.validation.base import ValidationBase

if TYPE_CHECKING:
    from kato.data_layers.service.repository_service import RepositoryService


class TaskBranchPushValidator(ValidationBase):
    def __init__(self, repository_service: RepositoryService) -> None:
        self._repository_service = repository_service

    def validate(
        self,
        repositories: list[object],
        repository_branches: dict[str, str],
    ) -> None:
        for repository in repositories:
            branch_name = repository_branches.get(repository.id, '')
            if not branch_name:
                raise ValueError(
                    f'missing task branch name for repository {repository.id}'
                )
            self._repository_service._ensure_branch_is_pushable(
                repository.local_path,
                branch_name,
                repository,
            )
