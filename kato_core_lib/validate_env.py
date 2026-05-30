from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from shutil import which

from kato_core_lib.helpers.dotenv_utils import parse_dotenv_text
from kato_core_lib.helpers.kato_config_utils import (
    is_bedrock_model,
    is_openrouter_model,
)
from kato_core_lib.helpers.text_utils import (
    alphanumeric_lower_text,
    normalized_lower_text,
    normalized_text,
)


TRUE_VALUES = {'1', 'true', 'yes', 'on'}
GITHUB_TOKEN_KEYS = ('GITHUB_API_TOKEN', 'GITHUB_API_TOKEN')
GITLAB_TOKEN_KEYS = ('GITLAB_API_TOKEN',)
BITBUCKET_TOKEN_KEYS = ('BITBUCKET_API_TOKEN',)
PROVIDER_TOKEN_ENV_KEYS = {
    'github': GITHUB_TOKEN_KEYS,
    'gitlab': GITLAB_TOKEN_KEYS,
    'bitbucket': BITBUCKET_TOKEN_KEYS,
}
SHARED_REQUIRED_AGENT_KEYS = (
    'REPOSITORY_ROOT_PATH',
)
OPENHANDS_REQUIRED_AGENT_KEYS = (
    'OPENHANDS_BASE_URL',
    'OPENHANDS_API_KEY',
)
SUPPORTED_AGENT_BACKENDS = ('openhands', 'claude')
REQUIRED_AGENT_KEYS_BY_PLATFORM = {
    'youtrack': (
        'YOUTRACK_API_BASE_URL',
        'YOUTRACK_API_TOKEN',
        'YOUTRACK_PROJECT',
        'YOUTRACK_ASSIGNEE',
    ),
    'jira': (
        'JIRA_API_BASE_URL',
        'JIRA_API_TOKEN',
        'JIRA_PROJECT',
        'JIRA_ASSIGNEE',
    ),
    'github': (
        'GITHUB_API_BASE_URL',
        'GITHUB_OWNER',
        'GITHUB_REPO',
        'GITHUB_ASSIGNEE',
    ),
    'gitlab': (
        'GITLAB_API_BASE_URL',
        'GITLAB_PROJECT',
        'GITLAB_ASSIGNEE',
    ),
    'bitbucket': (
        'BITBUCKET_API_BASE_URL',
        'BITBUCKET_WORKSPACE',
        'BITBUCKET_REPO_SLUG',
        'BITBUCKET_ASSIGNEE',
        'BITBUCKET_API_EMAIL',
    ),
}
EMAIL_REQUIREMENTS = {
    'KATO_FAILURE_EMAIL_ENABLED': (
        'failure',
        [
            'KATO_FAILURE_EMAIL_TEMPLATE_ID',
            'KATO_FAILURE_EMAIL_TO',
            'KATO_FAILURE_EMAIL_SENDER_EMAIL',
        ],
    ),
    'KATO_COMPLETION_EMAIL_ENABLED': (
        'completion',
        [
            'KATO_COMPLETION_EMAIL_TEMPLATE_ID',
            'KATO_COMPLETION_EMAIL_TO',
            'KATO_COMPLETION_EMAIL_SENDER_EMAIL',
        ],
    ),
}
logger = logging.getLogger(__name__)


def _read_env_file(path: str | None) -> dict[str, str]:
    if not path:
        return {}

    env_path = Path(path)
    if not env_path.exists():
        raise FileNotFoundError(f'env file not found: {path}')

    return parse_dotenv_text(env_path.read_text(encoding='utf-8'))


def _build_env(env_file: str | None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(_read_env_file(env_file))
    return env


def _is_enabled(value: str | None) -> bool:
    return normalized_lower_text(value) in TRUE_VALUES


def _missing(env: dict[str, str], keys: list[str]) -> list[str]:
    return [key for key in keys if not normalized_text(env.get(key, ''))]


def _has_any(env: dict[str, str], keys: tuple[str, ...] | list[str]) -> bool:
    return any(normalized_text(env.get(key, '')) for key in keys)


def validate_agent_env(env: dict[str, str]) -> list[str]:
    errors = []
    issue_platform = _configured_issue_platform(env)
    backend = _configured_agent_backend(env)
    errors.extend(_validate_agent_backend(backend))
    errors.extend(_validate_issue_platform(issue_platform))
    errors.extend(_validate_required_agent_keys(env, issue_platform, backend))
    errors.extend(_validate_repository_root_path(env))
    errors.extend(_validate_agent_email_env(env))
    errors.extend(_validate_repository_provider_env(env))
    errors.extend(_validate_issue_state_queue_env(env, issue_platform))
    return errors


def _validate_repository_root_path(env: dict[str, str]) -> list[str]:
    path = normalized_text(env.get('REPOSITORY_ROOT_PATH', ''))
    if not path:
        return []
    if not Path(path).expanduser().is_dir():
        return [
            f'REPOSITORY_ROOT_PATH does not exist or is not a directory: {path}'
        ]
    return []


def _configured_agent_backend(env: dict[str, str]) -> str:
    raw = normalized_lower_text(env.get('KATO_AGENT_BACKEND', ''))
    if raw in {'', 'openhands', 'open-hands', 'open_hands'}:
        return 'openhands'
    if raw in {'claude', 'claude-code', 'claude_code', 'claude-cli', 'claude_cli'}:
        return 'claude'
    return raw


def _validate_agent_backend(backend: str) -> list[str]:
    if backend in SUPPORTED_AGENT_BACKENDS:
        return []
    return [
        f'unsupported KATO_AGENT_BACKEND: {backend}; '
        f'supported values are {", ".join(SUPPORTED_AGENT_BACKENDS)}'
    ]


def _required_agent_keys(issue_platform: str, backend: str) -> list[str]:
    platform_keys = REQUIRED_AGENT_KEYS_BY_PLATFORM.get(
        issue_platform,
        REQUIRED_AGENT_KEYS_BY_PLATFORM['youtrack'],
    )
    keys = [*platform_keys, *SHARED_REQUIRED_AGENT_KEYS]
    if backend == 'openhands':
        keys.extend(OPENHANDS_REQUIRED_AGENT_KEYS)
    return keys


def _configured_issue_platform(env: dict[str, str]) -> str:
    return str(
        env.get('KATO_ISSUE_PLATFORM')
        or 'youtrack'
    ).strip().lower()


def _validate_issue_platform(issue_platform: str) -> list[str]:
    if issue_platform in REQUIRED_AGENT_KEYS_BY_PLATFORM:
        return []
    return [f'unsupported issue platform: {issue_platform}']


def _validate_required_agent_keys(
    env: dict[str, str],
    issue_platform: str,
    backend: str,
) -> list[str]:
    errors = [
        f'missing required agent env var: {key}'
        for key in _missing(env, _required_agent_keys(issue_platform, backend))
    ]
    provider_token_keys = PROVIDER_TOKEN_ENV_KEYS.get(issue_platform)
    if provider_token_keys and not _has_any(env, provider_token_keys):
        errors.append(f'missing required agent env var: {provider_token_keys[0]}')
    return errors


def _validate_agent_email_env(env: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for enabled_key, (label, required_keys) in EMAIL_REQUIREMENTS.items():
        if not _is_enabled(env.get(enabled_key)):
            continue
        for key in _missing(env, required_keys):
            errors.append(f'{label} email is enabled but {key} is missing')
    return errors


def _validate_repository_provider_env(env: dict[str, str]) -> list[str]:
    """No-op at startup.

    Auto-discovery used to walk every git folder under
    ``REPOSITORY_ROOT_PATH`` here and demand provider creds for each
    one — slow on big project roots and noisy when most of those repos
    aren't kato's concern. The runtime inventory now resolves repos
    lazily (per task, with a tag-name fast path), so credential
    requirements surface naturally when kato actually tries to use a
    repository. Kept as a hook so future per-platform pre-flight
    checks have a place to plug in.
    """
    del env  # unused on purpose
    return []


def _validate_issue_state_queue_env(env: dict[str, str], issue_platform: str) -> list[str]:
    platform_prefix = {
        'youtrack': 'YOUTRACK',
        'jira': 'JIRA',
        'github': 'GITHUB_ISSUES',
        'gitlab': 'GITLAB_ISSUES',
        'bitbucket': 'BITBUCKET_ISSUES',
    }.get(issue_platform)
    if not platform_prefix:
        return []

    issue_states = _split_env_states(env.get(f'{platform_prefix}_ISSUE_STATES', ''))
    progress_state = normalized_text(env.get(f'{platform_prefix}_PROGRESS_STATE', ''))
    review_state = normalized_text(env.get(f'{platform_prefix}_REVIEW_STATE', ''))
    invalid_states = []
    for state_name, label in ((progress_state, 'progress'), (review_state, 'review')):
        normalized_state = _normalized_state_token(state_name)
        if normalized_state and normalized_state in {
            _normalized_state_token(value) for value in issue_states
        }:
            invalid_states.append(f'{label} state "{state_name}"')

    if not invalid_states:
        return []

    return [
        f'{platform_prefix}_ISSUE_STATES must not include '
        + ' or '.join(invalid_states)
    ]


def _split_env_states(value: str | None) -> list[str]:
    return [normalized_text(state) for state in str(value or '').split(',') if normalized_text(state)]


def _normalized_state_token(value: str) -> str:
    return alphanumeric_lower_text(value)


def validate_openhands_env(env: dict[str, str]) -> list[str]:
    errors = []
    if not normalized_text(env.get('OH_SECRET_KEY', '')):
        errors.append('missing required OpenHands env var: OH_SECRET_KEY')

    model = normalized_text(env.get('OPENHANDS_LLM_MODEL', ''))
    if not model:
        errors.append('missing required OpenHands env var: OPENHANDS_LLM_MODEL')
        return errors

    errors.extend(
        _validate_openhands_model_auth(
            env,
            model,
            'OPENHANDS_LLM_API_KEY',
            'OPENHANDS_LLM_BASE_URL',
        )
    )
    errors.extend(_validate_openhands_testing_container_env(env))
    return errors


def _validate_openhands_testing_container_env(env: dict[str, str]) -> list[str]:
    if _is_enabled(env.get('OPENHANDS_SKIP_TESTING')):
        return []
    if not _is_enabled(env.get('OPENHANDS_TESTING_CONTAINER_ENABLED')):
        return []

    errors: list[str] = []
    for key in _missing(
        env,
        [
            'OPENHANDS_TESTING_BASE_URL',
            'OPENHANDS_TESTING_LLM_MODEL',
        ],
    ):
        errors.append(f'dedicated testing container requires {key}')

    testing_model = normalized_text(env.get('OPENHANDS_TESTING_LLM_MODEL', ''))
    if testing_model:
        errors.extend(
            _validate_openhands_model_auth(
                env,
                testing_model,
                'OPENHANDS_TESTING_LLM_API_KEY',
                'OPENHANDS_TESTING_LLM_BASE_URL',
            )
        )
    return errors


def _validate_openhands_model_auth(
    env: dict[str, str],
    model: str,
    api_key_key: str,
    base_url_key: str = '',
) -> list[str]:
    if is_bedrock_model(model):
        has_bearer = bool(normalized_text(env.get('AWS_BEARER_TOKEN_BEDROCK', '')))
        has_access_key_flow = not _missing(
            env,
            ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_REGION_NAME'],
        )
        if not has_bearer and not has_access_key_flow:
            return [
                'bedrock model requires AWS_BEARER_TOKEN_BEDROCK or '
                'AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_REGION_NAME'
            ]
        return []

    errors = [f'{model} requires {key}' for key in _missing(env, [api_key_key])]
    if is_openrouter_model(model) and base_url_key:
        errors.extend(
            [f'{model} requires {key}' for key in _missing(env, [base_url_key])]
        )
    return errors


def validate_claude_env(env: dict[str, str]) -> list[str]:
    """Validate Claude CLI backend environment variables."""
    errors: list[str] = []
    binary = normalized_text(env.get('KATO_CLAUDE_BINARY', '')) or 'claude'
    if not Path(binary).is_absolute():
        if which(binary) is None:
            errors.append(
                f'KATO_AGENT_BACKEND=claude requires the Claude CLI binary "{binary}" '
                'to be installed and on PATH. Install Claude Code from '
                'https://docs.claude.com/en/docs/claude-code/setup or set '
                'KATO_CLAUDE_BINARY to its absolute path.'
            )
    elif not Path(binary).exists():
        errors.append(
            f'KATO_CLAUDE_BINARY points to a path that does not exist: {binary}'
        )

    timeout_raw = normalized_text(env.get('KATO_CLAUDE_TIMEOUT_SECONDS', ''))
    if timeout_raw:
        try:
            timeout_value = int(timeout_raw)
            if timeout_value < 60:
                errors.append('KATO_CLAUDE_TIMEOUT_SECONDS must be at least 60')
        except ValueError:
            errors.append(
                f'KATO_CLAUDE_TIMEOUT_SECONDS must be an integer; got {timeout_raw}'
            )

    max_turns_raw = normalized_text(env.get('KATO_CLAUDE_MAX_TURNS', ''))
    if max_turns_raw:
        try:
            int(max_turns_raw)
        except ValueError:
            errors.append(
                f'KATO_CLAUDE_MAX_TURNS must be an integer when set; got {max_turns_raw}'
            )
    return errors


def _validate(mode: str, env: dict[str, str]) -> list[str]:
    if mode == 'agent':
        return validate_agent_env(env)
    if mode == 'openhands':
        if _configured_agent_backend(env) == 'claude':
            return validate_claude_env(env)
        return validate_openhands_env(env)
    errors = validate_agent_env(env)
    if _configured_agent_backend(env) == 'claude':
        errors.extend(validate_claude_env(env))
    else:
        errors.extend(validate_openhands_env(env))
    return errors


def validate_environment(
    mode: str = 'all',
    env: dict[str, str] | None = None,
    env_file: str | None = None,
) -> None:
    """Validate environment settings and raise on invalid configuration."""
    effective_env = dict(env) if env is not None else _build_env(env_file)
    errors = _validate(mode, effective_env)
    if errors:
        raise ValueError('\n'.join(errors))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    parser = argparse.ArgumentParser(description='Validate kato environment.')
    parser.add_argument(
        '--mode',
        choices=['agent', 'openhands', 'all'],
        default='all',
    )
    parser.add_argument('--env-file')
    args = parser.parse_args()

    env = _build_env(args.env_file)
    errors = _validate(args.mode, env)
    if errors:
        for error in errors:
            logger.info(error)
        return 1

    logger.info('%s environment validation passed', args.mode)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
