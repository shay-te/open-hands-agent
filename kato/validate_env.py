from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from kato.helpers.kato_config_utils import (
    is_bedrock_model,
    is_openrouter_model,
)
from kato.helpers.repository_discovery_utils import discover_git_repositories
from kato.helpers.text_utils import (
    alphanumeric_lower_text,
    normalized_lower_text,
    normalized_text,
    text_from_attr,
)


TRUE_VALUES = {'1', 'true', 'yes', 'on'}
GITHUB_TOKEN_KEYS = ('GITHUB_API_TOKEN',)
GITLAB_TOKEN_KEYS = ('GITLAB_API_TOKEN',)
BITBUCKET_TOKEN_KEYS = ('BITBUCKET_API_TOKEN',)
BITBUCKET_REPOSITORY_REQUIRED_KEYS = (
    'BITBUCKET_API_TOKEN',
    'BITBUCKET_API_EMAIL',
)
PROVIDER_TOKEN_ENV_KEYS = {
    'github': GITHUB_TOKEN_KEYS,
    'gitlab': GITLAB_TOKEN_KEYS,
    'bitbucket': BITBUCKET_TOKEN_KEYS,
}
SHARED_REQUIRED_AGENT_KEYS = (
    'REPOSITORY_ROOT_PATH',
    'OPENHANDS_BASE_URL',
    'OPENHANDS_API_KEY',
)
REQUIRED_AGENT_KEYS_BY_PLATFORM = {
    'youtrack': (
        'YOUTRACK_BASE_URL',
        'YOUTRACK_TOKEN',
        'YOUTRACK_PROJECT',
        'YOUTRACK_ASSIGNEE',
    ),
    'jira': (
        'JIRA_BASE_URL',
        'JIRA_TOKEN',
        'JIRA_PROJECT',
        'JIRA_ASSIGNEE',
    ),
    'github': (
        'GITHUB_ISSUES_BASE_URL',
        'GITHUB_ISSUES_OWNER',
        'GITHUB_ISSUES_REPO',
        'GITHUB_ISSUES_ASSIGNEE',
    ),
    'gitlab': (
        'GITLAB_ISSUES_BASE_URL',
        'GITLAB_ISSUES_PROJECT',
        'GITLAB_ISSUES_ASSIGNEE',
    ),
    'bitbucket': (
        'BITBUCKET_ISSUES_BASE_URL',
        'BITBUCKET_ISSUES_WORKSPACE',
        'BITBUCKET_ISSUES_REPO_SLUG',
        'BITBUCKET_ISSUES_ASSIGNEE',
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

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


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
    errors.extend(_validate_issue_platform(issue_platform))
    errors.extend(_validate_required_agent_keys(env, issue_platform))
    errors.extend(_validate_agent_email_env(env))
    errors.extend(_validate_repository_provider_env(env))
    errors.extend(_validate_issue_state_queue_env(env, issue_platform))
    return errors


def _required_agent_keys(issue_platform: str) -> list[str]:
    platform_keys = REQUIRED_AGENT_KEYS_BY_PLATFORM.get(
        issue_platform,
        REQUIRED_AGENT_KEYS_BY_PLATFORM['youtrack'],
    )
    return [*platform_keys, *SHARED_REQUIRED_AGENT_KEYS]


def _configured_issue_platform(env: dict[str, str]) -> str:
    return str(
        env.get('KATO_ISSUE_PLATFORM')
        or env.get('KATO_TICKET_SYSTEM')
        or 'youtrack'
    ).strip().lower()


def _validate_issue_platform(issue_platform: str) -> list[str]:
    if issue_platform in REQUIRED_AGENT_KEYS_BY_PLATFORM:
        return []
    return [f'unsupported issue platform: {issue_platform}']


def _validate_required_agent_keys(
    env: dict[str, str],
    issue_platform: str,
) -> list[str]:
    errors = [
        f'missing required agent env var: {key}'
        for key in _missing(env, _required_agent_keys(issue_platform))
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
    repository_root_path = normalized_text(env.get('REPOSITORY_ROOT_PATH', ''))
    if not repository_root_path or not Path(repository_root_path).exists():
        return []

    errors: list[str] = []
    missing_keys: set[str] = set()
    for repository in discover_git_repositories(repository_root_path):
        provider = normalized_lower_text(text_from_attr(repository, 'provider'))
        token_keys = PROVIDER_TOKEN_ENV_KEYS.get(provider, ())
        if token_keys and not _has_any(env, token_keys):
            missing_keys.add(token_keys[0])
        if provider == 'bitbucket':
            for key in BITBUCKET_REPOSITORY_REQUIRED_KEYS:
                if not normalized_text(env.get(key, '')):
                    missing_keys.add(key)
    for key in sorted(missing_keys):
        errors.append(
            f'missing required repository provider env var: {key}'
        )
    return errors


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


def _validate(mode: str, env: dict[str, str]) -> list[str]:
    if mode == 'agent':
        return validate_agent_env(env)
    if mode == 'openhands':
        return validate_openhands_env(env)
    errors = validate_agent_env(env)
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
