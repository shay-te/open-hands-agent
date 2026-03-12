import json
import logging

from core_lib.jobs.job import Job

from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib


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
    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)

    def initialized(self, data_handler: OpenHandsAgentCoreLib) -> None:
        assert isinstance(data_handler, OpenHandsAgentCoreLib)
        self._data_handler = data_handler

    def run(self) -> None:
        try:
            results = collect_processing_results(self._data_handler.service)
            print(json.dumps(results))
        except Exception as exc:
            self.logger.exception('process_assigned_tasks_job failed')
            try:
                self._data_handler.service.notification_service.notify_failure(
                    'process_assigned_task_job',
                    exc,
                )
            except Exception:
                self.logger.exception(
                    'failed to send failure notification for process_assigned_task_job'
                )
            raise
