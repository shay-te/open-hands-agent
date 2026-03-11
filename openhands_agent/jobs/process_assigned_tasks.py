from core_lib.jobs.job import Job

from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib


class ProcessAssignedTasksJob(Job):
    def initialized(self, data_handler: OpenHandsAgentCoreLib) -> None:
        assert isinstance(data_handler, OpenHandsAgentCoreLib)
        self._data_handler = data_handler

    def run(self) -> list[dict[str, str]]:
        return self._data_handler.service.process_assigned_tasks()
