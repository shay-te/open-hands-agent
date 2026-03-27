from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import re

from openhands_agent.repository_discovery import (
    discover_git_repositories,
    read_git_remote_url,
)
from openhands_agent.validate_env import (
    _read_env_file,
    validate_agent_env,
    validate_openhands_env,
)

try:
    from core_lib.helpers.command_line import (
        input_string as core_input_string,
        input_yes_no as core_input_yes_no,
    )
    from core_lib.helpers.validation import is_int as core_is_int
except (ImportError, ModuleNotFoundError):
    core_input_string = None
    core_input_yes_no = None
    core_is_int = None


ISSUE_PLATFORMS = ['youtrack', 'jira', 'github', 'gitlab', 'bitbucket']
UNQUOTED_ENV_VALUE_PATTERN = re.compile(r'^[A-Za-z0-9_./:@%+=,\-~]*$')
logger = logging.getLogger(__name__)
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

def input_yes_no(message: str, default: bool = True) -> bool:
    if core_input_yes_no is not None:
        return bool(core_input_yes_no(message, default=default))
    return _input_yes_no_local(message, default=default)


def input_bool(message: str, default: bool = True) -> bool:
    return input_yes_no(message, default)


def input_str(
    message: str,
    default: str | None = None,
    allow_empty: bool = False,
) -> str:
    if core_input_string is not None and default is None and not allow_empty:
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
        if _is_int(candidate):
            return int(candidate)
        logger.info('Please enter a valid integer.')


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
        logger.info(message)
        for number, value in options_by_number.items():
            suffix = ' (default)' if number == default_number else ''
            logger.info('%s. %s%s', number, value, suffix)
        raw_value = _input_str_local(
            'Select an option by number',
            default=default_number,
            allow_empty=default_number is not None,
        ).strip()
        selected = options_by_number.get(raw_value)
        if selected is not None:
            return selected
        logger.info('Please choose one of: %s', ", ".join(options_by_number))


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
        logger.info('This value is required.')


def _input_yes_no_local(message: str, default: bool = True) -> bool:
    default_hint = 'Yes' if default else 'No'
    while True:
        raw_value = _input_str_local(
            f'{message}, Yes/No (enter for {default_hint})',
            default='',
            allow_empty=True,
        ).strip().lower()
        if not raw_value:
            return default
        if raw_value in {'y', 'yes'}:
            return True
        if raw_value in {'n', 'no'}:
            return False
        logger.info('Please answer Yes or No.')


def _is_int(value: str) -> bool:
    if core_is_int is not None:
        return bool(core_is_int(value))
    try:
        int(value)
    except ValueError:
        return False
    return True


def build_configuration_values(
    defaults: dict[str, str],
) -> dict[str, str]:
    values: dict[str, str] = {}
    issue_platform = input_enum(
        'Where are your tasks tracked',
        ISSUE_PLATFORMS,
        default=_default_str(defaults, 'OPENHANDS_AGENT_ISSUE_PLATFORM', 'OPENHANDS_AGENT_TICKET_SYSTEM', fallback='youtrack'),
    )
    values['OPENHANDS_AGENT_ISSUE_PLATFORM'] = issue_platform
    values['OPENHANDS_AGENT_TICKET_SYSTEM'] = issue_platform

    values.update(_prompt_issue_platform(defaults, issue_platform))
    values.update(_prompt_repository(defaults))
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
            lines.append(f'{normalized_key}={_format_env_value(values[normalized_key])}')
            seen_keys.add(normalized_key)
        else:
            lines.append(line)

    for key in sorted(values):
        if key in seen_keys:
            continue
        lines.append(f'{key}={_format_env_value(values[key])}')

    return '\n'.join(lines) + '\n'


def _format_env_value(value: object) -> str:
    text = str(value)
    if text == '':
        return ''
    if UNQUOTED_ENV_VALUE_PATTERN.fullmatch(text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    parser = argparse.ArgumentParser(description='Interactively create the openhands-agent .env file.')
    parser.add_argument('--template', default='.env.example')
    parser.add_argument('--output', default='.env')
    parser.add_argument(
        '--compose-override-output',
        default='.docker-compose.selected-repos.yaml',
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
        logger.info('Configuration cancelled.')
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
    logger.info('Wrote configuration to %s', output_path)
    if selected_paths:
        logger.info('Wrote Docker Compose repository mounts to %s', compose_override_path)
    if errors:
        logger.info('The file was written, but a few required values still need attention:')
        for error in errors:
            logger.info('- %s', error)
        logger.info('Run make doctor after filling the remaining values.')
        return 0

    logger.info('Configuration looks valid. Next: make doctor && make run')
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


def _prompt_repository(defaults: dict[str, str]) -> dict[str, str]:
    if input_yes_no(
        'Scan a projects folder for checked-out repositories',
        default=True,
    ):
        discovered_values = _prompt_discovered_repository(defaults)
        if discovered_values is not None:
            return discovered_values

    values = _prompt_repository_fields(defaults)
    values['OPENHANDS_SANDBOX_VOLUMES'] = _build_sandbox_volumes(
        [values['REPOSITORY_ROOT_PATH']]
    )
    return values


def _prompt_repository_fields(defaults: dict[str, str]) -> dict[str, str]:
    repository_root_path = _normalize_repository_path(
        _default_str(defaults, 'REPOSITORY_ROOT_PATH', 'REPOSITORY_LOCAL_PATH', fallback='.')
    )
    repository_root_path = _normalize_repository_path(
        input_str(
            'Projects root folder containing checked-out repositories',
            default=repository_root_path,
        )
    )

    return {
        'REPOSITORY_ROOT_PATH': repository_root_path,
    }


def _prompt_discovered_repository(
    defaults: dict[str, str],
) -> dict[str, str] | None:
    projects_root = _normalize_repository_path(
        input_str(
            'Projects folder to scan for repositories',
            default=_default_projects_root(defaults),
        )
    )
    discovered = discover_git_repositories(projects_root)
    if not discovered:
        raise ValueError(f'no git repositories were found under {projects_root}')

    logger.info('Discovered repositories:')
    for index, repository in enumerate(discovered, start=1):
        remote_suffix = f' ({repository.remote_url})' if repository.remote_url else ''
        logger.info('%s. %s%s', index, repository.local_path, remote_suffix)

    discovered_defaults = dict(defaults)
    discovered_defaults['REPOSITORY_ROOT_PATH'] = projects_root
    values = _prompt_repository_fields(discovered_defaults)
    values['OPENHANDS_SANDBOX_VOLUMES'] = _build_sandbox_volumes([projects_root])
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


def _bool_to_env(value: bool) -> str:
    return 'true' if value else 'false'


def _default_projects_root(values: dict[str, str]) -> str:
    root_path = _default_str(values, 'REPOSITORY_ROOT_PATH')
    if root_path:
        return _normalize_repository_path(root_path)

    local_path = _default_str(values, 'REPOSITORY_LOCAL_PATH')
    if not local_path:
        return str(Path.cwd())

    normalized_path = Path(local_path).expanduser()
    if not normalized_path.is_absolute():
        normalized_path = (Path.cwd() / normalized_path).resolve()
    return str(normalized_path.parent if normalized_path.name else normalized_path)


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
            logger.info('%s', exc)
            continue
        if numbers:
            return numbers
        if allow_empty:
            return []
        logger.info('Select at least one repository number.')


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


_read_git_remote_url = read_git_remote_url


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

    root_path = _default_str(values, 'REPOSITORY_ROOT_PATH')
    if root_path:
        return [_normalize_repository_path(root_path)]

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
