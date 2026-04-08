from __future__ import annotations

from typing import TYPE_CHECKING

from kato.validation.base import ValidationBase

if TYPE_CHECKING:
    from kato.data_layers.service.implementation_service import ImplementationService


class TaskModelAccessValidator(ValidationBase):
    def __init__(
        self,
        implementation_service: ImplementationService,
    ) -> None:
        self._implementation_service = implementation_service

    def validate(self, task) -> None:
        self._implementation_service.validate_model_access()
