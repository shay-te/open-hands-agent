import hydra
from omegaconf import DictConfig

from openhands_agent.jobs.process_assigned_tasks import collect_processing_results
from openhands_agent.logging_utils import configure_logger
from openhands_agent.openhands_agent_instance import OpenHandsAgentInstance


@hydra.main(
    version_base=None,
    config_path='config',
    config_name='openhands_agent_core_lib',
)
def main(cfg: DictConfig) -> int:
    logger = configure_logger(cfg.core_lib.app.name)
    OpenHandsAgentInstance.init(cfg)
    app = OpenHandsAgentInstance.get()
    app.logger = getattr(app, 'logger', None) or logger
    app.logger.info('starting openhands agent')
    try:
        results = collect_processing_results(app.service)
    except Exception as exc:
        app.logger.exception('failed to process assigned task')
        try:
            app.service.notification_service.notify_failure('process_assigned_task', exc)
        except Exception:
            app.logger.exception('failed to send failure notification for process_assigned_task')
        raise
    app.logger.info('processed %s items', len(results))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
