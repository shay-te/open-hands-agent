from pathlib import Path

from alembic.config import Config
from core_lib.data_layers.data.data_helpers import build_url
from omegaconf import DictConfig


def _build_sqlalchemy_url(cfg: DictConfig) -> str:
    url_cfg = cfg.core_lib.data.sqlalchemy.config.url
    return build_url(
        protocol=url_cfg.protocol,
        username=url_cfg.username,
        password=url_cfg.password,
        host=url_cfg.host,
        port=url_cfg.port,
        path=url_cfg.path,
        file=url_cfg.file,
    )


def build_alembic_config(cfg: DictConfig) -> Config:
    alembic_cfg = Config()
    alembic_settings = cfg.core_lib.alembic
    package_dir = Path(__file__).resolve().parent
    script_location = Path(alembic_settings.script_location)
    if not script_location.is_absolute():
        script_location = package_dir / script_location

    alembic_cfg.set_main_option('script_location', str(script_location))
    alembic_cfg.set_main_option('sqlalchemy.url', _build_sqlalchemy_url(cfg))
    alembic_cfg.set_main_option('version_table', alembic_settings.version_table)
    alembic_cfg.set_main_option(
        'render_as_batch',
        str(bool(alembic_settings.render_as_batch)).lower(),
    )
    return alembic_cfg
