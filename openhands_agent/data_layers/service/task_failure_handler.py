from core_lib.data_layers.service.service import Service

from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.data_layers.service.repository_service import RepositoryService
from openhands_agent.data_layers.service.task_service import TaskService
from openhands_agent.helpers.error_handling_utils import run_best_effort
from openhands_agent.helpers.logging_utils import configure_logger
from openhands_agent.helpers.task_context_utils import PreparedTaskContext


class TaskFailureHandler(Service):
    def __init__(
        self,
        task_service: TaskService,
        repository_service: RepositoryService,
        notification_service: NotificationService,
        logger=None,
    ) -> None:
        self._task_service = task_service
        self._repository_service = repository_service
        self._notification_service = notification_service
        self.logger = logger or configure_logger(self.__class__.__name__)

    def handle_repository_resolution_failure(
        self,
        task: Task,
        error: Exception,
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        if self._is_repository_detection_failure(error):
            self._handle_repository_detection_failure(task, error)
            return
        self.handle_task_failure(task, error, prepared_task=prepared_task)

    def handle_task_failure(
        self,
        task: Task,
        error: Exception,
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        self._restore_task_repositories(task, prepared_task=prepared_task)
        self._report_task_failure(
            task,
            error,
            f'OpenHands agent could not safely process this task: {error}',
        )

    def handle_started_task_failure(
        self,
        task: Task,
        error: Exception,
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        self._restore_task_repositories(task, prepared_task=prepared_task)
        self._report_task_failure(
            task,
            error,
            f'OpenHands agent stopped working on this task: {error}',
            move_to_open=True,
        )

    def handle_testing_failure(
        self,
        task: Task,
        testing: dict[str, str | bool],
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        self._handle_unsuccessful_agent_result(
            task,
            testing,
            prepared_task=prepared_task,
            default_summary='testing agent reported the task is not ready',
            warning_log_message='testing failed for task %s: %s',
        )

    def handle_implementation_failure(
        self,
        task: Task,
        execution: dict[str, str | bool],
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        self._handle_unsuccessful_agent_result(
            task,
            execution,
            prepared_task=prepared_task,
            default_summary='implementation agent reported the task is not ready',
            warning_log_message='implementation failed for task %s: %s',
        )

    def handle_task_definition_failure(self, task: Task) -> None:
        self._log_task_step(task.id, 'recording task-definition skip comment')
        self._add_task_comment(
            task.id,
            'OpenHands agent skipped this task because the task definition is too thin '
            'to work from safely. Please add a clearer description or issue comment '
            'describing the expected change.',
            after_step='added task-definition skip comment',
            failure_log_message='failed to add task definition comment for task %s',
        )

    def _handle_repository_detection_failure(self, task: Task, error: Exception) -> None:
        self._log_task_step(task.id, 'recording repository detection skip comment')
        self._add_task_comment(
            task.id,
            'OpenHands agent skipped this task because it could not detect which repository '
            f'to use from the task content: {error}. '
            'Please mention the repository name or alias in the task summary or description.',
            after_step='added repository detection skip comment',
            failure_log_message='failed to add repository detection comment for task %s',
        )

    def _handle_unsuccessful_agent_result(
        self,
        task: Task,
        payload: dict[str, str | bool],
        *,
        prepared_task: PreparedTaskContext | None = None,
        default_summary: str,
        warning_log_message: str,
    ) -> None:
        summary = str(payload.get(Task.summary.key) or default_summary)
        self.logger.warning(warning_log_message, task.id, summary)
        self.handle_started_task_failure(
            task,
            RuntimeError(summary),
            prepared_task=prepared_task,
        )

    def _report_task_failure(
        self,
        task: Task,
        error: Exception,
        comment: str,
        *,
        move_to_open: bool = False,
    ) -> None:
        self._log_task_step(task.id, 'recording failure comment: %s', comment)
        self._add_task_comment(
            task.id,
            comment,
            after_step='added failure comment',
            failure_log_message='failed to add failure comment for task %s',
        )
        if move_to_open:
            self._move_task_to_open(task.id)
        run_best_effort(
            lambda: self._notification_service.notify_failure(
                'process_assigned_task',
                error,
                {Task.id.key: task.id},
            ),
            logger=self.logger,
            failure_log_message='failed to send failure notification for task %s',
            failure_args=(task.id,),
            default=False,
        )

    def _restore_task_repositories(
        self,
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> None:
        repositories = prepared_task.repositories if prepared_task is not None else []
        repositories = repositories or []
        if not repositories:
            return
        self._log_task_step(task.id, 'restoring repository branches after task rejection')
        try:
            self._repository_service.restore_task_repositories(
                repositories,
                force=True,
            )
        except Exception:
            self.logger.exception('failed to restore repositories for task %s', task.id)

    def _add_task_comment(
        self,
        task_id: str,
        comment: str,
        *,
        after_step: str = '',
        failure_log_message: str,
    ) -> bool:
        try:
            self._task_service.add_comment(task_id, comment)
            if after_step:
                self._log_task_step(task_id, after_step)
            return True
        except Exception:
            self.logger.exception(failure_log_message, task_id)
            return False

    def _move_task_to_open(self, task_id: str) -> bool:
        try:
            self._log_task_step(task_id, 'moving issue back to open')
            self._task_service.move_task_to_open(task_id)
            self._log_task_step(task_id, 'moved issue back to open')
            return True
        except Exception:
            self.logger.exception('failed to move task %s back to open', task_id)
            return False

    @staticmethod
    def _is_repository_detection_failure(error: Exception) -> bool:
        return isinstance(error, ValueError) and 'no configured repository matched task' in str(error)

    def _log_task_step(self, task_id: str, message: str, *args) -> None:
        formatted_message = message
        if args:
            try:
                formatted_message = message % args
            except Exception:
                formatted_message = ' '.join([message, *[str(arg) for arg in args]])
        self.logger.info('Mission %s: %s', task_id, formatted_message)
