from __future__ import annotations

from core_lib.jobs.job import Job

from kato.helpers.error_handling_utils import log_and_notify_failure
from kato.helpers.logging_utils import configure_logger
from kato.kato_core_lib import KatoCoreLib


def collect_processing_results(service) -> list[dict]:
    results = []
    for task in service.get_assigned_tasks():
        result = service.process_assigned_task(task)
        if result is not None:
            results.append(result)
    for comment in service.get_new_pull_request_comments():
        result = _process_review_comment_best_effort(service, comment)
        if result is not None:
            results.append(result)
    return results


def _process_review_comment_best_effort(service, comment) -> dict | None:
    try:
        return service.process_review_comment(comment)
    except Exception:
        return None


class ProcessAssignedTasksJob(Job):
    def __init__(self) -> None:
        self.logger = configure_logger(self.__class__.__name__)

    def initialized(self, data_handler: KatoCoreLib) -> None:
        assert isinstance(data_handler, KatoCoreLib)
        self._data_handler = data_handler

    def run(self) -> None:
        try:
            results = collect_processing_results(self._data_handler.service)
            self._log_scan_results(results)
        except Exception as exc:
            log_and_notify_failure(
                logger=self.logger,
                notification_service=self._data_handler.service.notification_service,
                operation_name='process_assigned_task_job',
                error=exc,
                failure_log_message='process_assigned_tasks_job failed',
                notification_failure_log_message=(
                    'failed to send failure notification for process_assigned_task_job'
                ),
            )
            raise

    def _log_scan_results(self, results: list[dict]) -> None:
        if results:
            self.logger.info(
                'completed processing results:\n%s',
                format_processing_results(results),
            )


def format_processing_results(results: list[dict]) -> str:
    return '\n'.join(
        f'- {_format_processing_result(result)}'
        for result in results
    )


def _format_processing_result(result: dict) -> str:
    status = str(result.get('status', 'unknown'))
    pull_request_id = result.get('pull_request_id')
    branch_name = result.get('branch_name')
    repository_id = result.get('repository_id')

    details: list[str] = [status]
    if pull_request_id:
        details.append(f'PR #{pull_request_id}')
    if branch_name:
        details.append(f'branch {branch_name}')
    if repository_id:
        details.append(f'repository {repository_id}')

    return ' | '.join(details)
