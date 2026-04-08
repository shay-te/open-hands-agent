from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kato.validation.base import ValidationBase
from kato.helpers.retry_utils import is_retryable_exception

if TYPE_CHECKING:
    from kato.data_layers.service.implementation_service import ImplementationService
    from kato.validation.repository_connections import (
        RepositoryConnectionsValidator,
    )
    from kato.data_layers.service.task_service import TaskService
    from kato.data_layers.service.testing_service import TestingService


@dataclass(frozen=True)
class DependencyValidationStep:
    service_name: str
    validate: Callable[[], None]
    max_retries: int


class StartupDependencyValidator(ValidationBase):
    def __init__(
        self,
        repository_connections_validator: RepositoryConnectionsValidator,
        task_service: TaskService,
        implementation_service: ImplementationService,
        testing_service: TestingService,
        skip_testing: bool,
    ) -> None:
        self._repository_connections_validator = repository_connections_validator
        self._task_service = task_service
        self._implementation_service = implementation_service
        self._testing_service = testing_service
        self._skip_testing = bool(skip_testing)

    def validate(self, logger) -> None:
        self._validate_repositories(logger)

        summaries: list[str] = []
        details: list[str] = []
        for step in self._dependency_steps():
            self._collect_validation_result(logger, step, summaries, details)

        if details:
            raise RuntimeError(
                'startup dependency validation failed:\n\n'
                + '\n'.join(f'- {summary}' for summary in summaries)
                + '\n\nDetails:\n\n'
                + '\n\n'.join(details)
            )

    def _validate_repositories(self, logger) -> None:
        try:
            self._repository_connections_validator.validate()
            logger.info('validated repositories connection')
        except Exception as exc:
            logger.error('failed to validate repositories connection: %s', exc)
            raise RuntimeError(str(exc)) from None

    def _dependency_steps(self) -> list[DependencyValidationStep]:
        steps = [
            DependencyValidationStep(
                self._task_service.provider_name,
                self._task_service.validate_connection,
                self._task_service.max_retries,
            ),
            DependencyValidationStep(
                'openhands',
                self._implementation_service.validate_connection,
                self._implementation_service.max_retries,
            ),
        ]
        if not self._skip_testing:
            steps.append(
                DependencyValidationStep(
                    'openhands_testing',
                    self._testing_service.validate_connection,
                    self._testing_service.max_retries,
                )
            )
        return steps

    def _collect_validation_result(
        self,
        logger,
        step: DependencyValidationStep,
        summaries: list[str],
        details: list[str],
    ) -> None:
        try:
            step.validate()
            logger.info('validated %s connection', step.service_name)
        except Exception as exc:
            logger.exception('failed to validate %s connection', step.service_name)
            summaries.append(
                self._validation_failure_summary(step.service_name, exc, step.max_retries)
            )
            details.append(f'[{step.service_name}]\n{traceback.format_exc().rstrip()}')

    @staticmethod
    def _validation_failure_summary(
        service_name: str,
        exc: Exception,
        max_retries: int,
    ) -> str:
        if is_retryable_exception(exc):
            return (
                f'unable to connect to {service_name} '
                f'(tried {max(1, max_retries)} times)'
            )
        return f'unable to validate {service_name}: {exc}'
