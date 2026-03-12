from __future__ import annotations

import argparse
import importlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

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


class PromptAdapter:
    def __init__(self) -> None:
        self._module = self._load_shell_utils_module()

    @staticmethod
    def _load_shell_utils_module():
        module_names = [
            'core_lib.utils.shell_utils',
            'core_lib.helpers.shell_utils',
            'core_lib.shell_utils',
            'core_lib.helpers.command_line',
        ]
        for module_name in module_names:
            try:
                return importlib.import_module(module_name)
            except ModuleNotFoundError:
                continue
        return None

    def input_yes_no(self, message: str, default: bool = True) -> bool:
        func = self._lookup('input_yes_no')
        if func is not None:
            try:
                return bool(func(message, default=default))
            except TypeError:
                return bool(func(message))
        return self._input_yes_no_local(message, default)

    def input_bool(self, message: str, default: bool = True) -> bool:
        func = self._lookup('input_bool')
        if func is not None:
            try:
                return bool(func(message, default=default))
            except TypeError:
                return bool(func(message))
        return self.input_yes_no(message, default)

    def input_str(
        self,
        message: str,
        default: str | None = None,
        allow_empty: bool = False,
    ) -> str:
        func = self._lookup('input_str', 'input_string')
        if func is not None and default is None and not allow_empty:
            try:
                return str(func(f'{message}: '))
            except TypeError:
                return str(func(message))
        return self._input_str_local(message, default=default, allow_empty=allow_empty)

    def input_int(self, message: str, default: int | None = None) -> int:
        func = self._lookup('input_int')
        if func is not None:
            try:
                return int(func(message, default=default))
            except TypeError:
                return int(func(message))
        while True:
            value = self._input_str_local(
                message,
                default='' if default is None else str(default),
                allow_empty=default is not None,
            )
            candidate = str(value).strip()
            if not candidate and default is not None:
                return default
            try:
                return int(candidate)
            except ValueError:
                print('Please enter a valid integer.')

    def input_enum(
        self,
        message: str,
        values: list[str],
        default: str | None = None,
    ) -> str:
        func = self._lookup('input_enum', 'input_options')
        if func is not None:
            try:
                selected = func(message, values, default=default)
            except TypeError:
                selected = func(message, values)
            if selected in values:
                return str(selected)
        options_text = '/'.join(values)
        while True:
            value = self._input_str_local(
                f'{message} ({options_text})',
                default=default,
                allow_empty=default is not None,
            ).strip()
            if value in values:
                return value
            print(f'Please choose one of: {options_text}')

    def input_list(
        self,
        message: str,
        default: list[str] | None = None,
    ) -> list[str]:
        func = self._lookup('input_list')
        if func is not None:
            try:
                result = func(message, default=default or [])
            except TypeError:
                result = func(message)
            if isinstance(result, list):
                return [str(item).strip() for item in result if str(item).strip()]
        default_value = ', '.join(default or [])
        raw_value = self._input_str_local(
            f'{message} (comma-separated)',
            default=default_value,
            allow_empty=True,
        )
        return [part.strip() for part in raw_value.split(',') if part.strip()]

    def input_email(
        self,
        message: str,
        default: str | None = None,
        allow_empty: bool = False,
    ) -> str:
        func = self._lookup('input_email')
        if func is not None:
            try:
                return str(func(message, default=default))
            except TypeError:
                return str(func(message))
        while True:
            value = self._input_str_local(message, default=default, allow_empty=allow_empty)
            if not value and allow_empty:
                return ''
            if '@' in value and '.' in value.split('@')[-1]:
                return value
            print('Please enter a valid email address.')

    def input_url(
        self,
        message: str,
        default: str | None = None,
        allow_empty: bool = False,
    ) -> str:
        func = self._lookup('input_url')
        if func is not None:
            try:
                return str(func(message, default=default))
            except TypeError:
                return str(func(message))
        while True:
            value = self._input_str_local(message, default=default, allow_empty=allow_empty)
            if not value and allow_empty:
                return ''
            parsed = urlparse(value)
            if parsed.scheme and parsed.netloc:
                return value
            print('Please enter a valid URL, for example https://api.github.com')

    def _lookup(self, *names: str):
        if self._module is None:
            return None
        for name in names:
            func = getattr(self._module, name, None)
            if callable(func):
                return func
        return None

    @staticmethod
    def _input_yes_no_local(message: str, default: bool = True) -> bool:
        suffix = 'Y/n' if default else 'y/N'
        while True:
            raw = input(f'{message} [{suffix}]: ').strip().lower()
            if not raw:
                return default
            if raw in {'y', 'yes'}:
                return True
            if raw in {'n', 'no'}:
                return False
            print('Please enter yes or no.')

    @staticmethod
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
    prompter: PromptAdapter,
    defaults: dict[str, str],
) -> dict[str, str]:
    values: dict[str, str] = {}
    issue_platform = prompter.input_enum(
        'Where are your tasks tracked',
        ISSUE_PLATFORMS,
        default=_default_str(defaults, 'OPENHANDS_AGENT_ISSUE_PLATFORM', 'OPENHANDS_AGENT_TICKET_SYSTEM', fallback='youtrack'),
    )
    code_platform = prompter.input_enum(
        'Which platform hosts your source code',
        CODE_PLATFORMS,
        default=_infer_code_platform(defaults),
    )
    values['OPENHANDS_AGENT_ISSUE_PLATFORM'] = issue_platform
    values['OPENHANDS_AGENT_TICKET_SYSTEM'] = issue_platform

    values.update(_prompt_issue_platform(prompter, defaults, issue_platform))
    values.update(_prompt_repository(prompter, defaults, code_platform))
    values.update(_prompt_openhands(prompter, defaults))
    values.update(_prompt_notifications(prompter, defaults))
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
    args = parser.parse_args(argv)

    template_path = Path(args.template)
    output_path = Path(args.output)

    template_env = _read_env_file(str(template_path))
    current_env = template_env.copy()
    if output_path.exists():
        current_env.update(_read_env_file(str(output_path)))

    prompter = PromptAdapter()
    if output_path.exists() and not prompter.input_yes_no(
        f'{output_path} already exists. Overwrite it',
        default=True,
    ):
        print('Configuration cancelled.')
        return 1

    values = current_env.copy()
    values.update(build_configuration_values(prompter, current_env))
    rendered = render_env_text(template_path.read_text(encoding='utf-8'), values)
    output_path.write_text(rendered, encoding='utf-8')

    errors = validate_agent_env(values)
    errors.extend(validate_openhands_env(values))
    print(f'Wrote configuration to {output_path}')
    if errors:
        print('The file was written, but a few required values still need attention:')
        for error in errors:
            print(f'- {error}')
        return 1

    print('Configuration looks valid. Next: make doctor && make run')
    return 0


def _prompt_issue_platform(
    prompter: PromptAdapter,
    defaults: dict[str, str],
    issue_platform: str,
) -> dict[str, str]:
    details = ISSUE_PLATFORM_DETAILS[issue_platform]
    values: dict[str, str] = {
        details['base_url_key']: prompter.input_url(
            f"{details['label']} base URL",
            default=_default_str(defaults, details['base_url_key'], fallback=details['default_base_url']),
        ),
        details['token_key']: prompter.input_str(
            f"{details['label']} token",
            default=_default_str(defaults, details['token_key']),
        ),
        details['assignee_key']: prompter.input_str(
            f"{details['label']} {details['assignee_label']}",
            default=_default_str(defaults, details['assignee_key']),
        ),
        details['review_state_field_key']: prompter.input_str(
            f"{details['label']} review state field",
            default=_default_str(defaults, details['review_state_field_key'], fallback=details['default_review_field']),
        ),
        details['review_state_key']: prompter.input_str(
            f"{details['label']} review state value",
            default=_default_str(defaults, details['review_state_key'], fallback=details['default_review_state']),
        ),
        details['issue_states_key']: ','.join(
            prompter.input_list(
                f"{details['label']} issue states to process",
                default=_default_list(defaults, details['issue_states_key'], details['default_issue_states']),
            )
        ),
    }

    if 'project_key' in details:
        values[details['project_key']] = prompter.input_str(
            f"{details['label']} {details['project_label']}",
            default=_default_str(defaults, details['project_key']),
        )
    if 'owner_key' in details:
        values[details['owner_key']] = prompter.input_str(
            f"{details['label']} {details['owner_label']}",
            default=_default_str(defaults, details['owner_key']),
        )
    if 'repo_key' in details:
        values[details['repo_key']] = prompter.input_str(
            f"{details['label']} {details['repo_label']}",
            default=_default_str(defaults, details['repo_key']),
        )
    if 'workspace_key' in details:
        values[details['workspace_key']] = prompter.input_str(
            f"{details['label']} {details['workspace_label']}",
            default=_default_str(defaults, details['workspace_key']),
        )
    if 'repo_slug_key' in details:
        values[details['repo_slug_key']] = prompter.input_str(
            f"{details['label']} {details['repo_slug_label']}",
            default=_default_str(defaults, details['repo_slug_key']),
        )
    if 'email_key' in details:
        values[details['email_key']] = prompter.input_email(
            f"{details['label']} user email for basic auth",
            default=_default_str(defaults, details['email_key']),
            allow_empty=True,
        )
    return values


def _prompt_repository(
    prompter: PromptAdapter,
    defaults: dict[str, str],
    code_platform: str,
) -> dict[str, str]:
    values = {
        'REPOSITORY_ID': prompter.input_str(
            'Repository id',
            default=_default_str(defaults, 'REPOSITORY_ID', fallback='primary'),
        ),
        'REPOSITORY_DISPLAY_NAME': prompter.input_str(
            'Repository display name',
            default=_default_str(defaults, 'REPOSITORY_DISPLAY_NAME', fallback='Primary Repository'),
        ),
        'REPOSITORY_LOCAL_PATH': prompter.input_str(
            'Local path to the checked-out repository',
            default=_default_str(defaults, 'REPOSITORY_LOCAL_PATH', fallback='.'),
        ),
        'REPOSITORY_BASE_URL': prompter.input_url(
            f'{code_platform.capitalize()} API base URL',
            default=_default_str(defaults, 'REPOSITORY_BASE_URL', fallback=DEFAULT_CODE_BASE_URLS[code_platform]),
        ),
        'REPOSITORY_TOKEN': prompter.input_str(
            f'{code_platform.capitalize()} repository token',
            default=_default_str(defaults, 'REPOSITORY_TOKEN'),
        ),
        'REPOSITORY_OWNER': prompter.input_str(
            'Repository owner, workspace, or group',
            default=_default_str(defaults, 'REPOSITORY_OWNER'),
        ),
        'REPOSITORY_REPO_SLUG': prompter.input_str(
            'Repository name or slug',
            default=_default_str(defaults, 'REPOSITORY_REPO_SLUG'),
        ),
    }
    set_destination_branch = prompter.input_bool(
        'Set an explicit destination branch',
        default=bool(_default_str(defaults, 'REPOSITORY_DESTINATION_BRANCH')),
    )
    values['REPOSITORY_DESTINATION_BRANCH'] = ''
    if set_destination_branch:
        values['REPOSITORY_DESTINATION_BRANCH'] = prompter.input_str(
            'Destination branch',
            default=_default_str(defaults, 'REPOSITORY_DESTINATION_BRANCH', fallback='main'),
        )
    return values


def _prompt_openhands(
    prompter: PromptAdapter,
    defaults: dict[str, str],
) -> dict[str, str]:
    values = {
        'OPENHANDS_BASE_URL': prompter.input_url(
            'OpenHands base URL',
            default=_default_str(defaults, 'OPENHANDS_BASE_URL', fallback='http://localhost:3000'),
        ),
        'OPENHANDS_API_KEY': prompter.input_str(
            'OpenHands API key',
            default=_default_str(defaults, 'OPENHANDS_API_KEY', fallback='local'),
        ),
        'OPENHANDS_AGENT_MAX_RETRIES': str(
            prompter.input_int(
                'Maximum retries for external API calls',
                default=int(_default_str(defaults, 'OPENHANDS_AGENT_MAX_RETRIES', fallback='5')),
            )
        ),
        'OPENHANDS_AGENT_STATE_FILE': prompter.input_str(
            'State file path',
            default=_default_str(defaults, 'OPENHANDS_AGENT_STATE_FILE', fallback='openhands_agent_state.json'),
        ),
        'OPENHANDS_LLM_MODEL': prompter.input_str(
            'OpenHands LLM model',
            default=_default_str(defaults, 'OPENHANDS_LLM_MODEL'),
        ),
    }
    if values['OPENHANDS_LLM_MODEL'].startswith('bedrock/'):
        auth_mode = prompter.input_enum(
            'How should OpenHands authenticate to Bedrock',
            ['access_keys', 'bearer_token'],
            default='access_keys' if _default_str(defaults, 'AWS_ACCESS_KEY_ID') else 'bearer_token',
        )
        if auth_mode == 'access_keys':
            values['AWS_ACCESS_KEY_ID'] = prompter.input_str(
                'AWS access key id',
                default=_default_str(defaults, 'AWS_ACCESS_KEY_ID'),
            )
            values['AWS_SECRET_ACCESS_KEY'] = prompter.input_str(
                'AWS secret access key',
                default=_default_str(defaults, 'AWS_SECRET_ACCESS_KEY'),
            )
            values['AWS_REGION_NAME'] = prompter.input_str(
                'AWS region name',
                default=_default_str(defaults, 'AWS_REGION_NAME', fallback='us-west-2'),
            )
            values['AWS_BEARER_TOKEN_BEDROCK'] = ''
        else:
            values['AWS_BEARER_TOKEN_BEDROCK'] = prompter.input_str(
                'AWS bearer token for Bedrock',
                default=_default_str(defaults, 'AWS_BEARER_TOKEN_BEDROCK'),
            )
            values['AWS_ACCESS_KEY_ID'] = ''
            values['AWS_SECRET_ACCESS_KEY'] = ''
            values['AWS_REGION_NAME'] = ''
        values['OPENHANDS_LLM_API_KEY'] = ''
        values['OPENHANDS_LLM_BASE_URL'] = ''
    else:
        values['OPENHANDS_LLM_API_KEY'] = prompter.input_str(
            'OpenHands LLM API key',
            default=_default_str(defaults, 'OPENHANDS_LLM_API_KEY'),
        )
        values['OPENHANDS_LLM_BASE_URL'] = prompter.input_url(
            'OpenHands LLM base URL',
            default=_default_str(defaults, 'OPENHANDS_LLM_BASE_URL'),
            allow_empty=True,
        )
    return values


def _prompt_notifications(
    prompter: PromptAdapter,
    defaults: dict[str, str],
) -> dict[str, str]:
    values: dict[str, str] = {}
    failure_enabled = prompter.input_yes_no(
        'Enable failure notification emails',
        default=_default_bool(defaults, 'OPENHANDS_AGENT_FAILURE_EMAIL_ENABLED'),
    )
    completion_enabled = prompter.input_yes_no(
        'Enable completion notification emails',
        default=_default_bool(defaults, 'OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED'),
    )

    values['OPENHANDS_AGENT_FAILURE_EMAIL_ENABLED'] = _bool_to_env(failure_enabled)
    values['OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED'] = _bool_to_env(completion_enabled)
    values['EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY'] = _default_str(defaults, 'EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY')
    values['SLACK_WEBHOOK_URL_ERRORS_EMAIL'] = _default_str(defaults, 'SLACK_WEBHOOK_URL_ERRORS_EMAIL')

    if failure_enabled or completion_enabled:
        values['EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY'] = prompter.input_str(
            'Email provider API key',
            default=_default_str(defaults, 'EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY'),
        )
        values['SLACK_WEBHOOK_URL_ERRORS_EMAIL'] = prompter.input_url(
            'Slack webhook URL for email errors',
            default=_default_str(defaults, 'SLACK_WEBHOOK_URL_ERRORS_EMAIL'),
            allow_empty=True,
        )

    values.update(
        _prompt_notification_block(
            prompter,
            defaults,
            enabled=failure_enabled,
            prefix='OPENHANDS_AGENT_FAILURE_EMAIL',
            label='failure',
        )
    )
    values.update(
        _prompt_notification_block(
            prompter,
            defaults,
            enabled=completion_enabled,
            prefix='OPENHANDS_AGENT_COMPLETION_EMAIL',
            label='completion',
        )
    )
    return values


def _prompt_notification_block(
    prompter: PromptAdapter,
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
        prompter.input_int(
            f'{label.capitalize()} email template id',
            default=int(_default_str(defaults, f'{prefix}_TEMPLATE_ID', fallback='0')),
        )
    )
    values[f'{prefix}_TO'] = prompter.input_email(
        f'{label.capitalize()} email recipient',
        default=_default_str(defaults, f'{prefix}_TO'),
    )
    values[f'{prefix}_SENDER_NAME'] = prompter.input_str(
        f'{label.capitalize()} email sender name',
        default=_default_str(defaults, f'{prefix}_SENDER_NAME', fallback='OpenHands Agent'),
    )
    values[f'{prefix}_SENDER_EMAIL'] = prompter.input_email(
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


if __name__ == '__main__':
    raise SystemExit(main())
