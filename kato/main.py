from __future__ import annotations

import signal
import time

import hydra
from omegaconf import DictConfig

from kato.helpers.logging_utils import configure_logger
from kato.helpers.shell_status_utils import (
    sleep_with_scan_spinner,
    supports_inline_status,
    sleep_with_warmup_countdown,
)
from kato.validate_env import validate_environment


class _ProcessAssignedTasksJobProxy:
    def __call__(self):
        from kato.jobs.process_assigned_tasks import ProcessAssignedTasksJob as _ProcessAssignedTasksJob

        return _ProcessAssignedTasksJob()


class _KatoInstanceProxy:
    @staticmethod
    def init(core_lib_cfg: DictConfig) -> None:
        from kato.kato_instance import KatoInstance as _KatoInstance

        _KatoInstance.init(core_lib_cfg)

    @staticmethod
    def get():
        from kato.kato_instance import KatoInstance as _KatoInstance

        return _KatoInstance.get()


ProcessAssignedTasksJob = _ProcessAssignedTasksJobProxy()
KatoInstance = _KatoInstanceProxy()


@hydra.main(
    version_base=None,
    config_path='config',
    config_name='kato_core_lib',
)
def main(cfg: DictConfig) -> int:
    logger = configure_logger(cfg.core_lib.app.name)
    try:
        validate_environment(mode='all')
    except ValueError as exc:
        logger.error('%s', exc)
        return 1
    try:
        KatoInstance.init(cfg)
    except RuntimeError as exc:
        if str(exc).startswith('startup dependency validation failed:') or str(exc).startswith('[Error] '):
            logger.error('%s', exc)
            return 1
        raise
    app = KatoInstance.get()
    app.logger = getattr(app, 'logger', None) or logger
    app.logger.info('Starting kato agent')
    _register_shutdown_hook(app)
    startup_delay_seconds, scan_interval_seconds = _task_scan_settings(cfg)
    _run_task_scan_loop(
        app,
        startup_delay_seconds=startup_delay_seconds,
        scan_interval_seconds=scan_interval_seconds,
    )
    return 0


def _register_shutdown_hook(app) -> None:
    def _shutdown(signum, frame):
        app.logger.info('shutting down kato agent (signal %s)', signum)
        try:
            app.service.shutdown()
        except Exception:
            app.logger.exception('error during shutdown cleanup')
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)


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
        if supports_inline_status():
            sleep_with_warmup_countdown(
                startup_delay_seconds,
                sleep_fn=sleep_fn,
            )
        else:
            app.logger.info(
                'Waiting %s before scanning tasks while Kato warms up',
                _formatted_duration_text(startup_delay_seconds),
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
            sleep_with_scan_spinner(
                scan_interval_seconds,
                status_text='Scanning for new tasks and comments',
                sleep_fn=sleep_fn,
            )


def _formatted_duration_text(seconds: float) -> str:
    normalized_seconds = float(seconds)
    rounded_seconds = int(normalized_seconds)
    if normalized_seconds == rounded_seconds:
        seconds_label = 'second' if rounded_seconds == 1 else 'seconds'
        return f'{rounded_seconds} {seconds_label}'
    return f'{normalized_seconds:.1f} seconds'


if __name__ == '__main__':
    raise SystemExit(main())
