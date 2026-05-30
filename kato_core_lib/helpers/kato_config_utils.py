from __future__ import annotations

from omegaconf import DictConfig

from kato_core_lib.helpers.text_utils import normalized_lower_text, normalized_text


AGENT_BACKEND_OPENHANDS = 'openhands'
AGENT_BACKEND_CLAUDE = 'claude'
SUPPORTED_AGENT_BACKENDS = (AGENT_BACKEND_OPENHANDS, AGENT_BACKEND_CLAUDE)


# Shared workflow-state value defaults. The progress/review entries are
# common to TaskService (which also tracks 'done') and TaskStateService
# (which tracks 'open' via its separate field-defaults map). Each service
# composes its own ``_STATE_VALUE_DEFAULTS`` from this base.
SHARED_STATE_VALUE_DEFAULTS = {
    'progress': 'In Progress',
    'review': 'In Review',
}


def configured_state_value(config: DictConfig, state_key: str, defaults: dict) -> str:
    """Read ``<state_key>_state`` from ``config`` (falling back to ``defaults``).

    Centralises the ``getattr(config, f'{state_key}_state', defaults[...])``
    accessor shared by the task services.
    """
    return getattr(config, f'{state_key}_state', defaults[state_key])


def resolved_agent_backend(open_cfg: DictConfig) -> str:
    """Return the configured agent backend, defaulting to OpenHands.

    Accepts ``claude``/``claude-code`` as aliases for the Claude CLI backend.
    """
    raw = normalized_lower_text(getattr(open_cfg, 'agent_backend', '') or '')
    if raw in {'claude', 'claude-code', 'claude_code', 'claude-cli', 'claude_cli'}:
        return AGENT_BACKEND_CLAUDE
    if raw in {'openhands', 'open-hands', 'open_hands', ''}:
        return AGENT_BACKEND_OPENHANDS
    raise ValueError(
        f'unsupported KATO_AGENT_BACKEND: {raw!r}; '
        f'supported values are: {", ".join(SUPPORTED_AGENT_BACKENDS)}'
    )


def parse_issue_states(config: DictConfig) -> list[str]:
    if hasattr(config, 'issue_states'):
        issue_states = config.issue_states
        if isinstance(issue_states, str):
            return [s.strip() for s in issue_states.split(',') if s.strip()]
        return [str(s).strip() for s in issue_states if str(s).strip()]
    return [config.issue_state]


def is_bedrock_model(model: str) -> bool:
    return normalized_text(model).startswith('bedrock/')


def is_openrouter_model(model: str) -> bool:
    return normalized_text(model).startswith('openrouter/')


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
    return normalized_text(getattr(openhands_cfg, key, ''))
