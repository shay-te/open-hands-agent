import time

import hydra
from omegaconf import DictConfig

from kato.jobs.process_assigned_tasks import ProcessAssignedTasksJob
from kato.helpers.logging_utils import configure_logger
from kato.kato_instance import KatoInstance


@hydra.main(
    version_base=None,
    config_path='config',
    config_name='kato_core_lib',
)
def main(cfg: DictConfig) -> int:
    logger = configure_logger(cfg.core_lib.app.name)
    try:
        KatoInstance.init(cfg)
    except RuntimeError as exc:
        if str(exc).startswith('startup dependency validation failed:') or str(exc).startswith('[Error] '):
            logger.error('%s', exc)
            return 1
        raise
    app = KatoInstance.get()
    app.logger = getattr(app, 'logger', None) or logger
    app.logger.info('starting kato agent')
    startup_delay_seconds, scan_interval_seconds = _task_scan_settings(cfg)
    _run_task_scan_loop(
        app,
        startup_delay_seconds=startup_delay_seconds,
        scan_interval_seconds=scan_interval_seconds,
    )
    return 0


def _task_scan_settings(cfg: DictConfig) -> tuple[float, float]:
    task_scan_cfg = cfg.kato.get('task_scan', {}) or {}
    return (
        float(task_scan_cfg.get('startup_delay_seconds', 30.0)),
        float(task_scan_cfg.get('scan_interval_seconds', 60.0)),
    )


def _run_task_scan_loop(
    app,
    *,
    startup_delay_seconds: float,
    scan_interval_seconds: float,
    sleep_fn=time.sleep,
    max_cycles: int | None = None,
) -> None:
    job = ProcessAssignedTasksJob()
    job.initialized(app)
    if startup_delay_seconds > 0:
        app.logger.info(
            'waiting %s seconds for Kato to warm up before scanning tasks',
            startup_delay_seconds,
        )
        sleep_fn(startup_delay_seconds)

    cycles = 0
    while True:
        try:
            job.run()
        except Exception:
            app.logger.warning(
                'task scan failed; retrying in %s seconds',
                scan_interval_seconds,
            )

        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return
        if scan_interval_seconds > 0:
            sleep_fn(scan_interval_seconds)


if __name__ == '__main__':
    raise SystemExit(main())
