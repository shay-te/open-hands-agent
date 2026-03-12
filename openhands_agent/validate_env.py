from __future__ import annotations

import argparse
import os
from pathlib import Path


TRUE_VALUES = {'1', 'true', 'yes', 'on'}


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
    return str(value or '').strip().lower() in TRUE_VALUES


def _missing(env: dict[str, str], keys: list[str]) -> list[str]:
    return [key for key in keys if not str(env.get(key, '')).strip()]


def validate_agent_env(env: dict[str, str]) -> list[str]:
    errors = []
    issue_platform = str(
        env.get('OPENHANDS_AGENT_ISSUE_PLATFORM')
        or env.get('OPENHANDS_AGENT_TICKET_SYSTEM')
        or 'youtrack'
    ).strip().lower()
    if issue_platform not in {'youtrack', 'jira', 'github', 'gitlab', 'bitbucket'}:
        errors.append(f'unsupported issue platform: {issue_platform}')
    required = _required_agent_keys(issue_platform)
    for key in _missing(env, required):
        errors.append(f'missing required agent env var: {key}')

    if _is_enabled(env.get('OPENHANDS_AGENT_FAILURE_EMAIL_ENABLED')):
        for key in _missing(
            env,
            [
                'OPENHANDS_AGENT_FAILURE_EMAIL_TEMPLATE_ID',
                'OPENHANDS_AGENT_FAILURE_EMAIL_TO',
                'OPENHANDS_AGENT_FAILURE_EMAIL_SENDER_EMAIL',
            ],
        ):
            errors.append(f'failure email is enabled but {key} is missing')

    if _is_enabled(env.get('OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED')):
        for key in _missing(
            env,
            [
                'OPENHANDS_AGENT_COMPLETION_EMAIL_TEMPLATE_ID',
                'OPENHANDS_AGENT_COMPLETION_EMAIL_TO',
                'OPENHANDS_AGENT_COMPLETION_EMAIL_SENDER_EMAIL',
            ],
        ):
            errors.append(f'completion email is enabled but {key} is missing')

    return errors


def _required_agent_keys(issue_platform: str) -> list[str]:
    shared_required = [
        'REPOSITORY_ID',
        'REPOSITORY_BASE_URL',
        'REPOSITORY_LOCAL_PATH',
        'REPOSITORY_TOKEN',
        'REPOSITORY_OWNER',
        'REPOSITORY_REPO_SLUG',
        'OPENHANDS_BASE_URL',
        'OPENHANDS_API_KEY',
    ]
    if issue_platform == 'jira':
        return [
            'JIRA_BASE_URL',
            'JIRA_TOKEN',
            'JIRA_PROJECT',
            'JIRA_ASSIGNEE',
            *shared_required,
        ]
    if issue_platform == 'github':
        return [
            'GITHUB_ISSUES_BASE_URL',
            'GITHUB_ISSUES_TOKEN',
            'GITHUB_ISSUES_OWNER',
            'GITHUB_ISSUES_REPO',
            'GITHUB_ISSUES_ASSIGNEE',
            *shared_required,
        ]
    if issue_platform == 'gitlab':
        return [
            'GITLAB_ISSUES_BASE_URL',
            'GITLAB_ISSUES_TOKEN',
            'GITLAB_ISSUES_PROJECT',
            'GITLAB_ISSUES_ASSIGNEE',
            *shared_required,
        ]
    if issue_platform == 'bitbucket':
        return [
            'BITBUCKET_ISSUES_BASE_URL',
            'BITBUCKET_ISSUES_TOKEN',
            'BITBUCKET_ISSUES_WORKSPACE',
            'BITBUCKET_ISSUES_REPO_SLUG',
            'BITBUCKET_ISSUES_ASSIGNEE',
            *shared_required,
        ]
    return [
        'YOUTRACK_BASE_URL',
        'YOUTRACK_TOKEN',
        'YOUTRACK_PROJECT',
        'YOUTRACK_ASSIGNEE',
        *shared_required,
    ]


def validate_openhands_env(env: dict[str, str]) -> list[str]:
    errors = []
    model = str(env.get('OPENHANDS_LLM_MODEL', '')).strip()
    if not model:
        return ['missing required OpenHands env var: OPENHANDS_LLM_MODEL']

    if model.startswith('bedrock/'):
        has_bearer = bool(str(env.get('AWS_BEARER_TOKEN_BEDROCK', '')).strip())
        has_access_key_flow = not _missing(
            env,
            ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_REGION_NAME'],
        )
        if not has_bearer and not has_access_key_flow:
            errors.append(
                'bedrock model requires AWS_BEARER_TOKEN_BEDROCK or '
                'AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_REGION_NAME'
            )
        return errors

    for key in _missing(env, ['OPENHANDS_LLM_API_KEY']):
        errors.append(f'{model} requires {key}')
    return errors


def _validate(mode: str, env: dict[str, str]) -> list[str]:
    if mode == 'agent':
        return validate_agent_env(env)
    if mode == 'openhands':
        return validate_openhands_env(env)
    errors = validate_agent_env(env)
    errors.extend(validate_openhands_env(env))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate openhands-agent environment.')
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
            print(error)
        return 1

    print(f'{args.mode} environment validation passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
