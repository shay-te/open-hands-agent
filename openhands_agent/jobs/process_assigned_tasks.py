from __future__ import annotations

from core_lib.jobs.job import Job


class ProcessAssignedTasksJob(Job):

    def initialized(self, data_handler) -> None:
        self.data_handler = data_handler

    def run(self) -> list[dict[str, str]]:
        return self.data_handler.process_assigned_tasks()
