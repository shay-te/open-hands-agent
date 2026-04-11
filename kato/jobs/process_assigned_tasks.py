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
        result = service.process_review_comment(comment)
        if result is not None:
            results.append(result)
    return results


class ProcessAssignedTasksJob(Job):
    _EMPTY_SCAN_FRAMES = ('/', '-', '\\', '|')

    def __init__(self) -> None:
        self.logger = configure_logger(self.__class__.__name__)
        self._empty_scan_frame_index = 0

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
            self.logger.info('completed processing results: %s', results)
            return

        frame = self._EMPTY_SCAN_FRAMES[
            self._empty_scan_frame_index % len(self._EMPTY_SCAN_FRAMES)
        ]
        self._empty_scan_frame_index += 1
        self.logger.info('Scanning for new tasks and comments %s', frame)
