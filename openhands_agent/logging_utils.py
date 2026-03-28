import logging
import os


_LOGGING_CONFIGURED = False
_DEFAULT_LOG_LEVEL = logging.WARNING


def _configured_log_level() -> int:
    configured_name = str(os.getenv('OPENHANDS_AGENT_LOG_LEVEL', 'WARNING') or '').strip().upper()
    return getattr(logging, configured_name, _DEFAULT_LOG_LEVEL)


def configure_logger(name: str) -> logging.Logger:
    global _LOGGING_CONFIGURED

    if not _LOGGING_CONFIGURED:
        logging.basicConfig(
            level=_configured_log_level(),
            format='%(asctime)s %(levelname)s %(name)s %(message)s',
        )
        _LOGGING_CONFIGURED = True

    return logging.getLogger(name)
