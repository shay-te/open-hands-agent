from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kato_core_lib.validation.base import ValidationBase
from kato_core_lib.helpers.retry_utils import is_retryable_exception

if TYPE_CHECKING:
    from kato_core_lib.data_layers.service.implementation_service import ImplementationService
    from kato_core_lib.validation.repository_connections import (
        RepositoryConnectionsValidator,
    )
    from kato_core_lib.data_layers.service.task_service import TaskService
    from kato_core_lib.data_layers.service.testing_service import TestingService


@dataclass(frozen=True)
class DependencyValidationStep(object):
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
        agent_backend: str = 'openhands',
    ) -> None:
        self._repository_connections_validator = repository_connections_validator
        self._task_service = task_service
        self._implementation_service = implementation_service
        self._testing_service = testing_service
        self._skip_testing = bool(skip_testing)
        self._agent_backend = (str(agent_backend or '').strip().lower() or 'openhands')

    def validate(self, logger) -> None:
        repo_step = DependencyValidationStep(
            self._repository_validation_label(),
            self._repository_connections_validator.validate,
            0,
        )
        dependency_steps = self._dependency_steps()
        all_steps = [repo_step] + dependency_steps
        total_steps = len(all_steps)

        # Log all steps upfront so output order is deterministic regardless
        # of which thread finishes first.
        for i, step in enumerate(all_steps, start=1):
            logger.info('%s', f'Validating connection ({i}/{total_steps}): {step.service_name}')

        with ThreadPoolExecutor(max_workers=total_steps) as executor:
            futures = [(step, executor.submit(step.validate)) for step in all_steps]

        repo_errors: list[tuple[str, str]] = []
        other_errors: list[tuple[str, str]] = []
        for step, future in futures:
            exc = future.exception()
            if exc is None:
                continue
            summary = self._validation_failure_summary(step.service_name, exc, step.max_retries)
            detail = f'[{step.service_name}]\n{exc}'
            if step is repo_step:
                repo_errors.append((summary, detail))
            else:
                other_errors.append((summary, detail))

        all_errors = repo_errors + other_errors
        if not all_errors:
            return

        summaries = [s for s, _ in all_errors]
        details = [d for _, d in all_errors]
        raise RuntimeError(
            'startup dependency validation failed:\n\n'
            + '\n'.join(f'- {summary}' for summary in summaries)
            + '\n\nDetails:\n\n'
            + '\n\n'.join(details)
        )

    def _dependency_steps(self) -> list[DependencyValidationStep]:
        backend_label = self._agent_backend
        steps = [
            DependencyValidationStep(
                self._task_service.provider_name,
                self._task_service.validate_connection,
                self._task_service.max_retries,
            ),
            DependencyValidationStep(
                backend_label,
                self._implementation_service.validate_connection,
                self._implementation_service.max_retries,
            ),
        ]
        if not self._skip_testing:
            steps.append(
                DependencyValidationStep(
                    f'{backend_label}_testing',
                    self._testing_service.validate_connection,
                    self._testing_service.max_retries,
                )
            )
        return steps

    def _repository_validation_label(self) -> str:
        # Read the *already-materialised* inventory, never the
        # ``.repositories`` property — that property would force the
        # lazy auto-discovery walk just to format a status line, which
        # is the whole reason startup got slow on big project roots.
        repository_service = getattr(
            self._repository_connections_validator,
            '_repository_service',
            None,
        )
        repositories = getattr(repository_service, '_repositories', None)
        if not isinstance(repositories, (list, tuple)) or not repositories:
            return 'repositories'
        repository_ids = [
            str(getattr(repository, 'id', '') or '').strip()
            for repository in repositories
            if str(getattr(repository, 'id', '') or '').strip()
        ]
        if not repository_ids:
            return 'repositories'
        return f'repositories ({", ".join(repository_ids)})'

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
