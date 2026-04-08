from __future__ import annotations

from omegaconf import DictConfig


def normalized_openhands_text(value: object) -> str:
    return str(value or '').strip()


def is_bedrock_model(model: str) -> bool:
    return normalized_openhands_text(model).startswith('bedrock/')


def is_openrouter_model(model: str) -> bool:
    return normalized_openhands_text(model).startswith('openrouter/')


def testing_container_enabled(openhands_cfg: DictConfig) -> bool:
    return bool(getattr(openhands_cfg, 'testing_container_enabled', False))


def skip_testing_enabled(openhands_cfg: DictConfig) -> bool:
    return bool(getattr(openhands_cfg, 'skip_testing', False))


def resolved_openhands_base_url(
    openhands_cfg: DictConfig,
    *,
    testing: bool = False,
) -> str:
    if testing and testing_container_enabled(openhands_cfg):
        return _normalized_openhands_attr(openhands_cfg, 'testing_base_url')
    return _normalized_openhands_attr(openhands_cfg, 'base_url')


def resolved_openhands_llm_settings(
    openhands_cfg: DictConfig,
    *,
    testing: bool = False,
) -> dict[str, str]:
    if testing and testing_container_enabled(openhands_cfg):
        return _llm_settings_from_config(
            openhands_cfg,
            model_key='testing_llm_model',
            base_url_key='testing_llm_base_url',
        )
    return _llm_settings_from_config(
        openhands_cfg,
        model_key='llm_model',
        base_url_key='llm_base_url',
    )


def _llm_settings_from_config(
    openhands_cfg: DictConfig,
    *,
    model_key: str,
    base_url_key: str,
) -> dict[str, str]:
    return {
        'llm_model': _normalized_openhands_attr(openhands_cfg, model_key),
        'llm_base_url': _normalized_openhands_attr(openhands_cfg, base_url_key),
    }


def _normalized_openhands_attr(openhands_cfg: DictConfig, key: str) -> str:
    return normalized_openhands_text(getattr(openhands_cfg, key, ''))
