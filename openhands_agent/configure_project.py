from __future__ import annotations

import argparse
import configparser
import os
from pathlib import Path
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from core_lib.helpers.command_line import (
    input_string as core_input_string,
    input_yes_no as core_input_yes_no,
)
from core_lib.helpers.validation import is_int as core_is_int

from openhands_agent.validate_env import (
    _read_env_file,
    validate_agent_env,
    validate_openhands_env,
)


ISSUE_PLATFORMS = ['youtrack', 'jira', 'github', 'gitlab', 'bitbucket']
CODE_PLATFORMS = ['bitbucket', 'github', 'gitlab']
DEFAULT_CODE_BASE_URLS = {
    'bitbucket': 'https://api.bitbucket.org/2.0',
    'github': 'https://api.github.com',
    'gitlab': 'https://gitlab.com/api/v4',
}
DISCOVERY_SKIP_DIRS = {
    '.git',
    '.hg',
    '.svn',
    '.venv',
    '__pycache__',
    'node_modules',
}
ISSUE_PLATFORM_DETAILS = {
    'youtrack': {
        'label': 'YouTrack',
        'base_url_key': 'YOUTRACK_BASE_URL',
        'token_key': 'YOUTRACK_TOKEN',
        'project_key': 'YOUTRACK_PROJECT',
        'project_label': 'project key',
        'assignee_key': 'YOUTRACK_ASSIGNEE',
        'assignee_label': 'assignee login',
        'review_state_field_key': 'YOUTRACK_REVIEW_STATE_FIELD',
        'review_state_key': 'YOUTRACK_REVIEW_STATE',
        'issue_states_key': 'YOUTRACK_ISSUE_STATES',
        'default_base_url': 'https://your-company.youtrack.cloud',
        'default_review_field': 'State',
        'default_review_state': 'In Review',
        'default_issue_states': ['Todo', 'Open'],
    },
    'jira': {
        'label': 'Jira',
        'base_url_key': 'JIRA_BASE_URL',
        'token_key': 'JIRA_TOKEN',
        'project_key': 'JIRA_PROJECT',
        'project_label': 'project key',
        'assignee_key': 'JIRA_ASSIGNEE',
        'assignee_label': 'assignee account id or username',
        'review_state_field_key': 'JIRA_REVIEW_STATE_FIELD',
        'review_state_key': 'JIRA_REVIEW_STATE',
        'issue_states_key': 'JIRA_ISSUE_STATES',
        'default_base_url': 'https://your-company.atlassian.net',
        'default_review_field': 'status',
        'default_review_state': 'In Review',
        'default_issue_states': ['To Do', 'Open'],
        'email_key': 'JIRA_EMAIL',
    },
    'github': {
        'label': 'GitHub Issues',
        'base_url_key': 'GITHUB_ISSUES_BASE_URL',
        'token_key': 'GITHUB_ISSUES_TOKEN',
        'owner_key': 'GITHUB_ISSUES_OWNER',
        'owner_label': 'repository owner or organization',
        'repo_key': 'GITHUB_ISSUES_REPO',
        'repo_label': 'issues repository name',
        'assignee_key': 'GITHUB_ISSUES_ASSIGNEE',
        'assignee_label': 'assignee login',
        'review_state_field_key': 'GITHUB_ISSUES_REVIEW_STATE_FIELD',
        'review_state_key': 'GITHUB_ISSUES_REVIEW_STATE',
        'issue_states_key': 'GITHUB_ISSUES_ISSUE_STATES',
        'default_base_url': 'https://api.github.com',
        'default_review_field': 'labels',
        'default_review_state': 'In Review',
        'default_issue_states': ['open'],
    },
    'gitlab': {
        'label': 'GitLab Issues',
        'base_url_key': 'GITLAB_ISSUES_BASE_URL',
        'token_key': 'GITLAB_ISSUES_TOKEN',
        'project_key': 'GITLAB_ISSUES_PROJECT',
        'project_label': 'project path or numeric id',
        'assignee_key': 'GITLAB_ISSUES_ASSIGNEE',
        'assignee_label': 'assignee username',
        'review_state_field_key': 'GITLAB_ISSUES_REVIEW_STATE_FIELD',
        'review_state_key': 'GITLAB_ISSUES_REVIEW_STATE',
        'issue_states_key': 'GITLAB_ISSUES_ISSUE_STATES',
        'default_base_url': 'https://gitlab.com/api/v4',
        'default_review_field': 'labels',
        'default_review_state': 'In Review',
        'default_issue_states': ['opened'],
    },
    'bitbucket': {
        'label': 'Bitbucket Issues',
        'base_url_key': 'BITBUCKET_ISSUES_BASE_URL',
        'token_key': 'BITBUCKET_ISSUES_TOKEN',
        'workspace_key': 'BITBUCKET_ISSUES_WORKSPACE',
        'workspace_label': 'workspace',
        'repo_slug_key': 'BITBUCKET_ISSUES_REPO_SLUG',
        'repo_slug_label': 'issues repository slug',
        'assignee_key': 'BITBUCKET_ISSUES_ASSIGNEE',
        'assignee_label': 'assignee username',
        'review_state_field_key': 'BITBUCKET_ISSUES_REVIEW_STATE_FIELD',
        'review_state_key': 'BITBUCKET_ISSUES_REVIEW_STATE',
        'issue_states_key': 'BITBUCKET_ISSUES_ISSUE_STATES',
        'default_base_url': 'https://api.bitbucket.org/2.0',
        'default_review_field': 'state',
        'default_review_state': 'resolved',
        'default_issue_states': ['new', 'open'],
    },
}


@dataclass(frozen=True)
class DiscoveredRepository:
    local_path: str
    remote_url: str
    provider: str
    owner: str
    repo_slug: str


def input_yes_no(message: str, default: bool = True) -> bool:
    return bool(core_input_yes_no(message, default=default))


def input_bool(message: str, default: bool = True) -> bool:
    return input_yes_no(message, default)


def input_str(
    message: str,
    default: str | None = None,
    allow_empty: bool = False,
) -> str:
    if default is None and not allow_empty:
        return str(core_input_string(f'{message}: '))
    return _input_str_local(message, default=default, allow_empty=allow_empty)


def input_int(message: str, default: int | None = None) -> int:
    while True:
        value = _input_str_local(
            message,
            default='' if default is None else str(default),
            allow_empty=default is not None,
        )
        candidate = str(value).strip()
        if not candidate and default is not None:
            return default
        if core_is_int(candidate):
            return int(candidate)
        print('Please enter a valid integer.')


def input_enum(
    message: str,
    values: list[str],
    default: str | None = None,
) -> str:
    options_by_number = {str(index): value for index, value in enumerate(values, start=1)}
    default_number = next(
        (number for number, value in options_by_number.items() if value == default),
        None,
    )
    while True:
        print(message)
        for number, value in options_by_number.items():
            suffix = ' (default)' if number == default_number else ''
            print(f'{number}. {value}{suffix}')
        raw_value = _input_str_local(
            'Select an option by number',
            default=default_number,
            allow_empty=default_number is not None,
        ).strip()
        selected = options_by_number.get(raw_value)
        if selected is not None:
            return selected
        print(f'Please choose one of: {", ".join(options_by_number)}')


def input_list(
    message: str,
    default: list[str] | None = None,
) -> list[str]:
    default_value = ', '.join(default or [])
    raw_value = input_str(
        f'{message} (comma-separated)',
        default=default_value,
        allow_empty=True,
    )
    return [part.strip() for part in raw_value.split(',') if part.strip()]


def _input_str_local(
    message: str,
    default: str | None = None,
    allow_empty: bool = False,
) -> str:
    prompt = message
    if default not in {None, ''}:
        prompt = f'{prompt} [{default}]'
    prompt = f'{prompt}: '
    while True:
        value = input(prompt).strip()
        if value:
            return value
        if default is not None:
            return default
        if allow_empty:
            return ''
        print('This value is required.')


def build_configuration_values(
    defaults: dict[str, str],
) -> dict[str, str]:
    values: dict[str, str] = {}
    issue_platform = input_enum(
        'Where are your tasks tracked',
        ISSUE_PLATFORMS,
        default=_default_str(defaults, 'OPENHANDS_AGENT_ISSUE_PLATFORM', 'OPENHANDS_AGENT_TICKET_SYSTEM', fallback='youtrack'),
    )
    code_platform = input_enum(
        'Which platform hosts your source code',
        CODE_PLATFORMS,
        default=_infer_code_platform(defaults),
    )
    values['OPENHANDS_AGENT_ISSUE_PLATFORM'] = issue_platform
    values['OPENHANDS_AGENT_TICKET_SYSTEM'] = issue_platform

    values.update(_prompt_issue_platform(defaults, issue_platform))
    values.update(_prompt_repository(defaults, code_platform))
    values.update(_prompt_openhands(defaults))
    values.update(_prompt_notifications(defaults))
    return values


def render_env_text(template_text: str, values: dict[str, str]) -> str:
    lines: list[str] = []
    seen_keys: set[str] = set()
    for line in template_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in line:
            lines.append(line)
            continue
        key, _ = line.split('=', 1)
        normalized_key = key.strip()
        if normalized_key in values:
            lines.append(f'{normalized_key}={values[normalized_key]}')
            seen_keys.add(normalized_key)
        else:
            lines.append(line)

    for key in sorted(values):
        if key in seen_keys:
            continue
        lines.append(f'{key}={values[key]}')

    return '\n'.join(lines) + '\n'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Interactively create the openhands-agent .env file.')
    parser.add_argument('--template', default='.env.example')
    parser.add_argument('--output', default='.env')
    parser.add_argument(
        '--compose-override-output',
        default='.docker-compose.selected-repos.yml',
    )
    args = parser.parse_args(argv)

    template_path = Path(args.template)
    output_path = Path(args.output)
    compose_override_path = Path(args.compose_override_output)

    template_env = _read_env_file(str(template_path))
    current_env = template_env.copy()
    if output_path.exists():
        current_env.update(_read_env_file(str(output_path)))

    if output_path.exists() and not input_yes_no(
        f'{output_path} already exists. Overwrite it',
        default=True,
    ):
        print('Configuration cancelled.')
        return 1

    values = current_env.copy()
    values.update(build_configuration_values(current_env))
    rendered = render_env_text(template_path.read_text(encoding='utf-8'), values)
    output_path.write_text(rendered, encoding='utf-8')
    selected_paths = _selected_repository_paths(values)
    if selected_paths:
        compose_override_path.write_text(
            render_selected_repository_compose_override(selected_paths),
            encoding='utf-8',
        )

    errors = validate_agent_env(values)
    errors.extend(validate_openhands_env(values))
    print(f'Wrote configuration to {output_path}')
    if selected_paths:
        print(f'Wrote Docker Compose repository mounts to {compose_override_path}')
    if errors:
        print('The file was written, but a few required values still need attention:')
        for error in errors:
            print(f'- {error}')
        return 1

    print('Configuration looks valid. Next: make doctor && make run')
    return 0


def _prompt_issue_platform(
    defaults: dict[str, str],
    issue_platform: str,
) -> dict[str, str]:
    details = ISSUE_PLATFORM_DETAILS[issue_platform]
    values: dict[str, str] = {
        details['base_url_key']: input_str(
            f"{details['label']} base URL",
            default=_default_str(defaults, details['base_url_key'], fallback=details['default_base_url']),
        ),
        details['token_key']: input_str(
            f"{details['label']} token",
            default=_default_str(defaults, details['token_key']),
        ),
        details['assignee_key']: input_str(
            f"{details['label']} {details['assignee_label']}",
            default=_default_str(defaults, details['assignee_key']),
        ),
        details['review_state_field_key']: input_str(
            f"{details['label']} review state field",
            default=_default_str(defaults, details['review_state_field_key'], fallback=details['default_review_field']),
        ),
        details['review_state_key']: input_str(
            f"{details['label']} review state value",
            default=_default_str(defaults, details['review_state_key'], fallback=details['default_review_state']),
        ),
        details['issue_states_key']: ','.join(
            input_list(
                f"{details['label']} issue states to process",
                default=_default_list(defaults, details['issue_states_key'], details['default_issue_states']),
            )
        ),
    }

    if 'project_key' in details:
        values[details['project_key']] = input_str(
            f"{details['label']} {details['project_label']}",
            default=_default_str(defaults, details['project_key']),
        )
    if 'owner_key' in details:
        values[details['owner_key']] = input_str(
            f"{details['label']} {details['owner_label']}",
            default=_default_str(defaults, details['owner_key']),
        )
    if 'repo_key' in details:
        values[details['repo_key']] = input_str(
            f"{details['label']} {details['repo_label']}",
            default=_default_str(defaults, details['repo_key']),
        )
    if 'workspace_key' in details:
        values[details['workspace_key']] = input_str(
            f"{details['label']} {details['workspace_label']}",
            default=_default_str(defaults, details['workspace_key']),
        )
    if 'repo_slug_key' in details:
        values[details['repo_slug_key']] = input_str(
            f"{details['label']} {details['repo_slug_label']}",
            default=_default_str(defaults, details['repo_slug_key']),
        )
    if 'email_key' in details:
        values[details['email_key']] = input_str(
            f"{details['label']} user email for basic auth",
            default=_default_str(defaults, details['email_key']),
            allow_empty=True,
        )
    return values


def _prompt_repository(
    defaults: dict[str, str],
    code_platform: str,
) -> dict[str, str]:
    if input_yes_no(
        'Scan a projects folder for checked-out repositories',
        default=True,
    ):
        discovered_values = _prompt_discovered_repository(
            defaults,
            code_platform,
        )
        if discovered_values is not None:
            return discovered_values

    values = _prompt_repository_fields(defaults, code_platform)
    allowed_paths = [
        values['REPOSITORY_LOCAL_PATH'],
        *_prompt_additional_repository_paths(defaults, values['REPOSITORY_LOCAL_PATH']),
    ]
    values['OPENHANDS_SANDBOX_VOLUMES'] = _build_sandbox_volumes(allowed_paths)
    return values


def _prompt_repository_fields(
    defaults: dict[str, str],
    code_platform: str,
    *,
    prompt_for_local_path: bool = True,
    prompt_for_repository_identity: bool = True,
) -> dict[str, str]:
    local_path = _normalize_repository_path(_default_str(defaults, 'REPOSITORY_LOCAL_PATH', fallback='.'))
    if prompt_for_local_path:
        local_path = _normalize_repository_path(
            input_str(
                'Local path to the checked-out repository',
                default=local_path,
            )
        )
    repository_id = _default_str(defaults, 'REPOSITORY_ID', fallback='primary')
    repository_display_name = _default_str(
        defaults,
        'REPOSITORY_DISPLAY_NAME',
        fallback='Primary Repository',
    )
    if prompt_for_repository_identity:
        repository_id = input_str(
            'Repository id',
            default=repository_id,
        )
        repository_display_name = input_str(
            'Repository display name',
            default=repository_display_name,
        )

    values = {
        'REPOSITORY_ID': repository_id,
        'REPOSITORY_DISPLAY_NAME': repository_display_name,
        'REPOSITORY_LOCAL_PATH': local_path,
        'REPOSITORY_BASE_URL': input_str(
            f'{code_platform.capitalize()} API base URL',
            default=_default_str(defaults, 'REPOSITORY_BASE_URL', fallback=DEFAULT_CODE_BASE_URLS[code_platform]),
        ),
        'REPOSITORY_TOKEN': input_str(
            f'{code_platform.capitalize()} repository token',
            default=_default_str(defaults, 'REPOSITORY_TOKEN'),
        ),
        'REPOSITORY_OWNER': input_str(
            'Repository owner, workspace, or group',
            default=_default_str(defaults, 'REPOSITORY_OWNER'),
        ),
        'REPOSITORY_REPO_SLUG': input_str(
            'Repository name or slug',
            default=_default_str(defaults, 'REPOSITORY_REPO_SLUG'),
        ),
    }
    set_destination_branch = input_bool(
        'Set an explicit destination branch',
        default=bool(_default_str(defaults, 'REPOSITORY_DESTINATION_BRANCH')),
    )
    values['REPOSITORY_DESTINATION_BRANCH'] = ''
    if set_destination_branch:
        values['REPOSITORY_DESTINATION_BRANCH'] = input_str(
            'Destination branch',
            default=_default_str(defaults, 'REPOSITORY_DESTINATION_BRANCH', fallback='main'),
        )
    return values


def _prompt_discovered_repository(
    defaults: dict[str, str],
    code_platform: str,
) -> dict[str, str] | None:
    projects_root = _normalize_repository_path(
        input_str(
            'Projects folder to scan for repositories',
            default=_default_projects_root(defaults),
        )
    )
    discovered = _discover_git_repositories(projects_root)
    if not discovered:
        raise ValueError(f'no git repositories were found under {projects_root}')

    print('Discovered repositories:')
    for index, repository in enumerate(discovered, start=1):
        remote_suffix = f' ({repository.remote_url})' if repository.remote_url else ''
        print(f'{index}. {repository.local_path}{remote_suffix}')

    primary_index = _prompt_repository_numbers(
        'Repository numbers to grant OpenHands access',
        len(discovered),
        allow_empty=False,
    )[0]
    selected_indexes = _prompt_repository_numbers(
        'Additional repository numbers to grant OpenHands access (comma-separated, optional)',
        len(discovered),
        allow_empty=True,
    )
    selected_repositories = [discovered[primary_index - 1]]
    selected_repositories.extend(
        discovered[index - 1]
        for index in selected_indexes
        if index != primary_index
    )

    discovered_defaults = dict(defaults)
    discovered_defaults.update(
        _repository_defaults_from_discovery(discovered[primary_index - 1], code_platform)
    )
    values = _prompt_repository_fields(
        discovered_defaults,
        code_platform,
        prompt_for_local_path=False,
        prompt_for_repository_identity=False,
    )
    allowed_paths = [
        values['REPOSITORY_LOCAL_PATH'],
        *[
            repository.local_path
            for repository in selected_repositories
            if repository.local_path != values['REPOSITORY_LOCAL_PATH']
        ],
    ]
    values['OPENHANDS_SANDBOX_VOLUMES'] = _build_sandbox_volumes(allowed_paths)
    return values


def _prompt_openhands(
    defaults: dict[str, str],
) -> dict[str, str]:
    values = {
        'OPENHANDS_BASE_URL': input_str(
            'OpenHands base URL',
            default=_default_str(defaults, 'OPENHANDS_BASE_URL', fallback='http://localhost:3000'),
        ),
        'OPENHANDS_API_KEY': input_str(
            'OpenHands API key',
            default=_default_str(defaults, 'OPENHANDS_API_KEY', fallback='local'),
        ),
        'OPENHANDS_AGENT_MAX_RETRIES': str(
            input_int(
                'Maximum retries for external API calls',
                default=int(_default_str(defaults, 'OPENHANDS_AGENT_MAX_RETRIES', fallback='5')),
            )
        ),
        'OPENHANDS_AGENT_STATE_FILE': input_str(
            'State file path',
            default=_default_str(defaults, 'OPENHANDS_AGENT_STATE_FILE', fallback='openhands_agent_state.json'),
        ),
        'OPENHANDS_LLM_MODEL': input_str(
            'OpenHands LLM model',
            default=_default_str(defaults, 'OPENHANDS_LLM_MODEL'),
        ),
    }
    if values['OPENHANDS_LLM_MODEL'].startswith('bedrock/'):
        auth_mode = input_enum(
            'How should OpenHands authenticate to Bedrock',
            ['access_keys', 'bearer_token'],
            default='access_keys' if _default_str(defaults, 'AWS_ACCESS_KEY_ID') else 'bearer_token',
        )
        if auth_mode == 'access_keys':
            values['AWS_ACCESS_KEY_ID'] = input_str(
                'AWS access key id',
                default=_default_str(defaults, 'AWS_ACCESS_KEY_ID'),
            )
            values['AWS_SECRET_ACCESS_KEY'] = input_str(
                'AWS secret access key',
                default=_default_str(defaults, 'AWS_SECRET_ACCESS_KEY'),
            )
            values['AWS_REGION_NAME'] = input_str(
                'AWS region name',
                default=_default_str(defaults, 'AWS_REGION_NAME', fallback='us-west-2'),
            )
            values['AWS_BEARER_TOKEN_BEDROCK'] = ''
        else:
            values['AWS_BEARER_TOKEN_BEDROCK'] = input_str(
                'AWS bearer token for Bedrock',
                default=_default_str(defaults, 'AWS_BEARER_TOKEN_BEDROCK'),
            )
            values['AWS_ACCESS_KEY_ID'] = ''
            values['AWS_SECRET_ACCESS_KEY'] = ''
            values['AWS_REGION_NAME'] = ''
        values['OPENHANDS_LLM_API_KEY'] = ''
        values['OPENHANDS_LLM_BASE_URL'] = ''
    else:
        values['OPENHANDS_LLM_API_KEY'] = input_str(
            'OpenHands LLM API key',
            default=_default_str(defaults, 'OPENHANDS_LLM_API_KEY'),
        )
        values['OPENHANDS_LLM_BASE_URL'] = input_str(
            'OpenHands LLM base URL',
            default=_default_str(defaults, 'OPENHANDS_LLM_BASE_URL'),
            allow_empty=True,
        )
    return values


def _prompt_notifications(
    defaults: dict[str, str],
) -> dict[str, str]:
    values: dict[str, str] = {}
    failure_enabled = input_yes_no(
        'Enable failure notification emails',
        default=_default_bool(defaults, 'OPENHANDS_AGENT_FAILURE_EMAIL_ENABLED'),
    )
    completion_enabled = input_yes_no(
        'Enable completion notification emails',
        default=_default_bool(defaults, 'OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED'),
    )

    values['OPENHANDS_AGENT_FAILURE_EMAIL_ENABLED'] = _bool_to_env(failure_enabled)
    values['OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED'] = _bool_to_env(completion_enabled)
    values['EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY'] = _default_str(defaults, 'EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY')
    values['SLACK_WEBHOOK_URL_ERRORS_EMAIL'] = _default_str(defaults, 'SLACK_WEBHOOK_URL_ERRORS_EMAIL')

    if failure_enabled or completion_enabled:
        values['EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY'] = input_str(
            'Email provider API key',
            default=_default_str(defaults, 'EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY'),
        )
        values['SLACK_WEBHOOK_URL_ERRORS_EMAIL'] = input_str(
            'Slack webhook URL for email errors',
            default=_default_str(defaults, 'SLACK_WEBHOOK_URL_ERRORS_EMAIL'),
            allow_empty=True,
        )

    values.update(
        _prompt_notification_block(
            defaults,
            enabled=failure_enabled,
            prefix='OPENHANDS_AGENT_FAILURE_EMAIL',
            label='failure',
        )
    )
    values.update(
        _prompt_notification_block(
            defaults,
            enabled=completion_enabled,
            prefix='OPENHANDS_AGENT_COMPLETION_EMAIL',
            label='completion',
        )
    )
    return values


def _prompt_notification_block(
    defaults: dict[str, str],
    enabled: bool,
    prefix: str,
    label: str,
) -> dict[str, str]:
    values = {
        f'{prefix}_TEMPLATE_ID': _default_str(defaults, f'{prefix}_TEMPLATE_ID', fallback='0'),
        f'{prefix}_TO': _default_str(defaults, f'{prefix}_TO'),
        f'{prefix}_SENDER_NAME': _default_str(defaults, f'{prefix}_SENDER_NAME', fallback='OpenHands Agent'),
        f'{prefix}_SENDER_EMAIL': _default_str(defaults, f'{prefix}_SENDER_EMAIL', fallback='noreply@example.com'),
    }
    if not enabled:
        return values

    values[f'{prefix}_TEMPLATE_ID'] = str(
        input_int(
            f'{label.capitalize()} email template id',
            default=int(_default_str(defaults, f'{prefix}_TEMPLATE_ID', fallback='0')),
        )
    )
    values[f'{prefix}_TO'] = input_str(
        f'{label.capitalize()} email recipient',
        default=_default_str(defaults, f'{prefix}_TO'),
    )
    values[f'{prefix}_SENDER_NAME'] = input_str(
        f'{label.capitalize()} email sender name',
        default=_default_str(defaults, f'{prefix}_SENDER_NAME', fallback='OpenHands Agent'),
    )
    values[f'{prefix}_SENDER_EMAIL'] = input_str(
        f'{label.capitalize()} email sender address',
        default=_default_str(defaults, f'{prefix}_SENDER_EMAIL', fallback='noreply@example.com'),
    )
    return values


def _default_str(
    values: dict[str, str],
    *keys: str,
    fallback: str = '',
) -> str:
    for key in keys:
        value = str(values.get(key, '') or '').strip()
        if value:
            return value
    return fallback


def _default_list(
    values: dict[str, str],
    key: str,
    fallback: list[str],
) -> list[str]:
    raw_value = str(values.get(key, '') or '').strip()
    if not raw_value:
        return list(fallback)
    return [part.strip() for part in raw_value.split(',') if part.strip()]


def _default_bool(values: dict[str, str], key: str) -> bool:
    return _default_str(values, key).lower() in {'1', 'true', 'yes', 'on'}


def _infer_code_platform(values: dict[str, str]) -> str:
    base_url = _default_str(values, 'REPOSITORY_BASE_URL').lower()
    if 'github' in base_url:
        return 'github'
    if 'gitlab' in base_url:
        return 'gitlab'
    return 'bitbucket'


def _bool_to_env(value: bool) -> str:
    return 'true' if value else 'false'


def _default_projects_root(values: dict[str, str]) -> str:
    local_path = _default_str(values, 'REPOSITORY_LOCAL_PATH')
    if not local_path:
        return str(Path.cwd())

    normalized_path = Path(local_path).expanduser()
    if not normalized_path.is_absolute():
        normalized_path = (Path.cwd() / normalized_path).resolve()
    return str(normalized_path.parent if normalized_path.name else normalized_path)


def _prompt_additional_repository_paths(
    defaults: dict[str, str],
    primary_path: str,
) -> list[str]:
    default_paths = [
        path
        for path in _selected_repository_paths(defaults)
        if path != primary_path
    ]
    raw_paths = input_list(
        'Additional checked-out repository folders to grant OpenHands access',
        default=default_paths,
    )
    paths = [_normalize_repository_path(path) for path in raw_paths]
    return [path for path in paths if path != primary_path]


def _prompt_repository_numbers(
    message: str,
    candidate_count: int,
    *,
    allow_empty: bool,
) -> list[int]:
    while True:
        raw_value = input_str(
            message,
            default='1' if not allow_empty else '',
            allow_empty=allow_empty,
        ).strip()
        if not raw_value:
            return []
        try:
            numbers = _parse_repository_numbers(raw_value, candidate_count)
        except ValueError as exc:
            print(str(exc))
            continue
        if numbers:
            return numbers
        if allow_empty:
            return []
        print('Select at least one repository number.')


def _parse_repository_numbers(raw_value: str, candidate_count: int) -> list[int]:
    numbers: list[int] = []
    seen: set[int] = set()
    for part in raw_value.split(','):
        candidate = part.strip()
        if not candidate:
            continue
        if not candidate.isdigit():
            raise ValueError('Enter repository numbers as comma-separated integers.')
        number = int(candidate)
        if number < 1 or number > candidate_count:
            raise ValueError(f'Repository number must be between 1 and {candidate_count}.')
        if number not in seen:
            numbers.append(number)
            seen.add(number)
    return numbers


def _discover_git_repositories(projects_root: str) -> list[DiscoveredRepository]:
    root_path = Path(projects_root).expanduser()
    if not root_path.exists() or not root_path.is_dir():
        return []

    repositories: list[DiscoveredRepository] = []
    for current_root, dir_names, file_names in os.walk(root_path):
        has_git_metadata = '.git' in dir_names or '.git' in file_names
        dir_names[:] = [
            directory
            for directory in dir_names
            if directory not in DISCOVERY_SKIP_DIRS
        ]
        if not has_git_metadata:
            continue
        repository_path = Path(current_root).resolve()
        repositories.append(_build_discovered_repository(repository_path))
        dir_names[:] = []

    repositories.sort(key=lambda repository: repository.local_path.lower())
    return repositories


def _build_discovered_repository(repository_path: Path) -> DiscoveredRepository:
    remote_url = _read_git_remote_url(repository_path)
    provider, owner, repo_slug = _parse_git_remote_url(remote_url)
    return DiscoveredRepository(
        local_path=str(repository_path),
        remote_url=remote_url,
        provider=provider,
        owner=owner,
        repo_slug=repo_slug,
    )


def _read_git_remote_url(repository_path: Path) -> str:
    config_path = _git_config_path(repository_path)
    if config_path is None or not config_path.exists():
        return ''

    parser = configparser.RawConfigParser(strict=False)
    try:
        parser.read(config_path, encoding='utf-8')
    except configparser.Error as exc:
        print(
            f'Warning: could not parse git config at {config_path}: {exc}. '
            'Repository discovery will continue without remote metadata.'
        )
        return ''
    if parser.has_option('remote "origin"', 'url'):
        return parser.get('remote "origin"', 'url').strip()

    for section in parser.sections():
        if section.startswith('remote "') and parser.has_option(section, 'url'):
            return parser.get(section, 'url').strip()
    return ''


def _git_config_path(repository_path: Path) -> Path | None:
    git_entry = repository_path / '.git'
    if git_entry.is_dir():
        return git_entry / 'config'
    if not git_entry.is_file():
        return None

    git_file_lines = git_entry.read_text(encoding='utf-8').splitlines()
    if not git_file_lines:
        return None

    first_line = git_file_lines[0].strip()
    if not first_line.startswith('gitdir:'):
        return None
    git_dir = first_line.split(':', 1)[1].strip()
    git_dir_path = Path(git_dir)
    if not git_dir_path.is_absolute():
        git_dir_path = (repository_path / git_dir_path).resolve()
    return git_dir_path / 'config'


def _parse_git_remote_url(remote_url: str) -> tuple[str, str, str]:
    if not remote_url:
        return '', '', ''

    host = ''
    path = ''
    if '://' in remote_url:
        parsed = urlparse(remote_url)
        host = str(parsed.hostname or '').lower()
        path = parsed.path.lstrip('/')
    else:
        match = re.match(r'[^@]+@([^:]+):(.+)', remote_url)
        if match is not None:
            host = match.group(1).lower()
            path = match.group(2)

    if not host or not path:
        return '', '', ''

    path = path.rstrip('/')
    if path.endswith('.git'):
        path = path[:-4]
    parts = [part for part in path.split('/') if part]
    if len(parts) < 2:
        return '', '', ''

    provider = ''
    if 'github' in host:
        provider = 'github'
    elif 'gitlab' in host:
        provider = 'gitlab'
    elif 'bitbucket' in host:
        provider = 'bitbucket'
    return provider, '/'.join(parts[:-1]), parts[-1]


def _repository_defaults_from_discovery(
    repository: DiscoveredRepository,
    code_platform: str,
) -> dict[str, str]:
    repo_slug = repository.repo_slug or Path(repository.local_path).name
    return {
        'REPOSITORY_ID': _repository_id_from_name(repo_slug),
        'REPOSITORY_DISPLAY_NAME': _display_name_from_repo_slug(repo_slug),
        'REPOSITORY_LOCAL_PATH': repository.local_path,
        'REPOSITORY_BASE_URL': DEFAULT_CODE_BASE_URLS[code_platform],
        'REPOSITORY_OWNER': repository.owner,
        'REPOSITORY_REPO_SLUG': repo_slug,
    }


def _repository_id_from_name(name: str) -> str:
    normalized = re.sub(r'[^a-z0-9._-]+', '-', name.strip().lower())
    return normalized.strip('-') or 'primary'


def _display_name_from_repo_slug(repo_slug: str) -> str:
    words = [part for part in re.split(r'[-_]+', repo_slug.strip()) if part]
    if not words:
        return 'Primary Repository'
    return ' '.join(word[:1].upper() + word[1:] for word in words)


def _normalize_repository_path(raw_path: str) -> str:
    path = Path(str(raw_path).strip()).expanduser()
    return str(path.resolve())


def _selected_repository_paths(values: dict[str, str]) -> list[str]:
    raw_mounts = _default_str(values, 'OPENHANDS_SANDBOX_VOLUMES')
    if raw_mounts:
        selected_paths: list[str] = []
        seen_paths: set[str] = set()
        for mount_spec in raw_mounts.split(','):
            candidate = mount_spec.strip()
            if not candidate:
                continue
            host_path = candidate.split(':', 1)[0].strip()
            if host_path and host_path not in seen_paths:
                selected_paths.append(host_path)
                seen_paths.add(host_path)
        if selected_paths:
            return selected_paths

    local_path = _default_str(values, 'REPOSITORY_LOCAL_PATH')
    if not local_path:
        return []
    return [_normalize_repository_path(local_path)]


def _build_sandbox_volumes(paths: list[str]) -> str:
    unique_paths: list[str] = []
    seen_paths: set[str] = set()
    for path in paths:
        normalized_path = _normalize_repository_path(path)
        if normalized_path in seen_paths:
            continue
        unique_paths.append(normalized_path)
        seen_paths.add(normalized_path)
    return ','.join(f'{path}:{path}:rw' for path in unique_paths)


def render_selected_repository_compose_override(paths: list[str]) -> str:
    unique_paths: list[str] = []
    seen_paths: set[str] = set()
    for path in paths:
        normalized_path = _normalize_repository_path(path)
        if normalized_path in seen_paths:
            continue
        unique_paths.append(normalized_path)
        seen_paths.add(normalized_path)

    lines = [
        'services:',
        '  openhands-agent:',
        '    volumes:',
    ]
    for path in unique_paths:
        lines.append(f"      - '{_yaml_quote(f'{path}:{path}:ro')}'")
    return '\n'.join(lines) + '\n'


def _yaml_quote(value: str) -> str:
    return value.replace("'", "''")


if __name__ == '__main__':
    raise SystemExit(main())
