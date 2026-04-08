from __future__ import annotations

from typing import TYPE_CHECKING

from kato.validation.base import ValidationBase

if TYPE_CHECKING:
    from kato.data_layers.service.repository_service import RepositoryService


class RepositoryConnectionsValidator(ValidationBase):
    def __init__(self, repository_service: RepositoryService) -> None:
        self._repository_service = repository_service

    def validate(self) -> None:
        self._repository_service._validate_inventory()
        self._repository_service._validate_git_executable()
        for repository in self._repository_service.repositories:
            self._repository_service._prepare_repository_access(repository)
            self._repository_service._validate_repository_git_access(repository)
