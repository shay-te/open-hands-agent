from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from openhands_agent.configure_project import (
    build_configuration_values,
    main,
    render_env_text,
)
from openhands_agent.validate_env import _read_env_file, validate_agent_env, validate_openhands_env


class _FakePrompter:
    def __init__(self, responses: dict[str, object]) -> None:
        self._responses = responses

    def _get(self, message: str):
        if message not in self._responses:
            raise AssertionError(f'unexpected prompt: {message}')
        return self._responses[message]

    def input_yes_no(self, message: str, default: bool = True) -> bool:
        return bool(self._get(message))

    def input_bool(self, message: str, default: bool = True) -> bool:
        return bool(self._get(message))

    def input_str(self, message: str, default: str | None = None, allow_empty: bool = False) -> str:
        return str(self._get(message))

    def input_int(self, message: str, default: int | None = None) -> int:
        return int(self._get(message))

    def input_enum(self, message: str, values: list[str], default: str | None = None) -> str:
        value = str(self._get(message))
        if value not in values:
            raise AssertionError(f'invalid enum response {value!r} for {message}')
        return value

    def input_list(self, message: str, default: list[str] | None = None) -> list[str]:
        value = self._get(message)
        if not isinstance(value, list):
            raise AssertionError(f'expected list response for {message}')
        return value

    def input_email(self, message: str, default: str | None = None, allow_empty: bool = False) -> str:
        return str(self._get(message))

    def input_url(self, message: str, default: str | None = None, allow_empty: bool = False) -> str:
        return str(self._get(message))


class ConfigureProjectTests(unittest.TestCase):
    def test_build_configuration_values_for_youtrack_and_github_repo(self) -> None:
        values = build_configuration_values(
            _FakePrompter(
                {
                    'Where are your tasks tracked': 'youtrack',
                    'Which platform hosts your source code': 'github',
                    'YouTrack base URL': 'https://youtrack.example',
                    'YouTrack token': 'yt-token',
                    'YouTrack assignee login': 'developer',
                    'YouTrack project key': 'PROJ',
                    'YouTrack review state field': 'State',
                    'YouTrack review state value': 'In Review',
                    'YouTrack issue states to process': ['Open', 'Ready for Dev'],
                    'Repository id': 'client',
                    'Repository display name': 'Client',
                    'Local path to the checked-out repository': './client',
                    'Github API base URL': 'https://api.github.com',
                    'Github repository token': 'gh-token',
                    'Repository owner, workspace, or group': 'shay-te',
                    'Repository name or slug': 'open-hands-agent',
                    'Set an explicit destination branch': True,
                    'Destination branch': 'main',
                    'OpenHands base URL': 'http://localhost:3000',
                    'OpenHands API key': 'local',
                    'Maximum retries for external API calls': 5,
                    'State file path': 'openhands_agent_state.json',
                    'OpenHands LLM model': 'openai/gpt-4o',
                    'OpenHands LLM API key': 'llm-key',
                    'OpenHands LLM base URL': 'https://api.openai.com/v1',
                    'Enable failure notification emails': False,
                    'Enable completion notification emails': False,
                }
            ),
            {},
        )

        self.assertEqual(values['OPENHANDS_AGENT_ISSUE_PLATFORM'], 'youtrack')
        self.assertEqual(values['OPENHANDS_AGENT_TICKET_SYSTEM'], 'youtrack')
        self.assertEqual(values['YOUTRACK_ISSUE_STATES'], 'Open,Ready for Dev')
        self.assertEqual(values['REPOSITORY_BASE_URL'], 'https://api.github.com')
        self.assertEqual(values['REPOSITORY_DESTINATION_BRANCH'], 'main')
        self.assertEqual(values['OPENHANDS_LLM_API_KEY'], 'llm-key')
        self.assertEqual(validate_agent_env(values), [])
        self.assertEqual(validate_openhands_env(values), [])

    def test_build_configuration_values_for_jira_and_bedrock(self) -> None:
        values = build_configuration_values(
            _FakePrompter(
                {
                    'Where are your tasks tracked': 'jira',
                    'Which platform hosts your source code': 'bitbucket',
                    'Jira base URL': 'https://company.atlassian.net',
                    'Jira token': 'jira-token',
                    'Jira assignee account id or username': 'dev-user',
                    'Jira project key': 'ENG',
                    'Jira review state field': 'status',
                    'Jira review state value': 'Code Review',
                    'Jira issue states to process': ['To Do', 'Selected for Development'],
                    'Jira user email for basic auth': 'dev@example.com',
                    'Repository id': 'backend',
                    'Repository display name': 'Backend',
                    'Local path to the checked-out repository': '.',
                    'Bitbucket API base URL': 'https://api.bitbucket.org/2.0',
                    'Bitbucket repository token': 'bb-token',
                    'Repository owner, workspace, or group': 'workspace',
                    'Repository name or slug': 'backend',
                    'Set an explicit destination branch': False,
                    'OpenHands base URL': 'http://localhost:3000',
                    'OpenHands API key': 'local',
                    'Maximum retries for external API calls': 7,
                    'State file path': 'state.json',
                    'OpenHands LLM model': 'bedrock/anthropic.claude-3-sonnet-20240229-v1:0',
                    'How should OpenHands authenticate to Bedrock': 'bearer_token',
                    'AWS bearer token for Bedrock': 'bedrock-token',
                    'Enable failure notification emails': True,
                    'Enable completion notification emails': False,
                    'Email provider API key': 'sendinblue-key',
                    'Slack webhook URL for email errors': '',
                    'Failure email template id': 42,
                    'Failure email recipient': 'ops@example.com',
                    'Failure email sender name': 'OpenHands Agent',
                    'Failure email sender address': 'noreply@example.com',
                }
            ),
            {},
        )

        self.assertEqual(values['JIRA_EMAIL'], 'dev@example.com')
        self.assertEqual(values['JIRA_ISSUE_STATES'], 'To Do,Selected for Development')
        self.assertEqual(values['AWS_BEARER_TOKEN_BEDROCK'], 'bedrock-token')
        self.assertEqual(values['OPENHANDS_LLM_API_KEY'], '')
        self.assertEqual(values['OPENHANDS_AGENT_FAILURE_EMAIL_ENABLED'], 'true')
        self.assertEqual(validate_agent_env(values), [])
        self.assertEqual(validate_openhands_env(values), [])

    def test_render_env_text_replaces_existing_keys(self) -> None:
        rendered = render_env_text(
            '# heading\nOPENHANDS_AGENT_ISSUE_PLATFORM=youtrack\nYOUTRACK_ISSUE_STATES=Todo,Open\n',
            {
                'OPENHANDS_AGENT_ISSUE_PLATFORM': 'jira',
                'YOUTRACK_ISSUE_STATES': 'Open,Ready for Dev',
                'JIRA_ISSUE_STATES': 'To Do,Open',
            },
        )

        self.assertIn('OPENHANDS_AGENT_ISSUE_PLATFORM=jira', rendered)
        self.assertIn('YOUTRACK_ISSUE_STATES=Open,Ready for Dev', rendered)
        self.assertIn('JIRA_ISSUE_STATES=To Do,Open', rendered)

    def test_main_writes_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            template_path = temp_path / '.env.example'
            output_path = temp_path / '.env'
            template_path.write_text(
                'OPENHANDS_AGENT_ISSUE_PLATFORM=youtrack\n'
                'OPENHANDS_AGENT_TICKET_SYSTEM=youtrack\n'
                'YOUTRACK_BASE_URL=\n'
                'YOUTRACK_TOKEN=\n'
                'YOUTRACK_PROJECT=\n'
                'YOUTRACK_ASSIGNEE=\n'
                'YOUTRACK_REVIEW_STATE_FIELD=State\n'
                'YOUTRACK_REVIEW_STATE=In Review\n'
                'YOUTRACK_ISSUE_STATES=Todo,Open\n'
                'REPOSITORY_ID=\n'
                'REPOSITORY_DISPLAY_NAME=\n'
                'REPOSITORY_LOCAL_PATH=.\n'
                'REPOSITORY_BASE_URL=\n'
                'REPOSITORY_TOKEN=\n'
                'REPOSITORY_OWNER=\n'
                'REPOSITORY_REPO_SLUG=\n'
                'REPOSITORY_DESTINATION_BRANCH=\n'
                'OPENHANDS_BASE_URL=http://localhost:3000\n'
                'OPENHANDS_API_KEY=local\n'
                'OPENHANDS_AGENT_MAX_RETRIES=5\n'
                'OPENHANDS_AGENT_STATE_FILE=openhands_agent_state.json\n'
                'OPENHANDS_LLM_MODEL=\n'
                'OPENHANDS_LLM_API_KEY=\n'
                'OPENHANDS_LLM_BASE_URL=\n'
                'OPENHANDS_AGENT_FAILURE_EMAIL_ENABLED=false\n'
                'OPENHANDS_AGENT_FAILURE_EMAIL_TEMPLATE_ID=0\n'
                'OPENHANDS_AGENT_FAILURE_EMAIL_TO=\n'
                'OPENHANDS_AGENT_FAILURE_EMAIL_SENDER_NAME=OpenHands Agent\n'
                'OPENHANDS_AGENT_FAILURE_EMAIL_SENDER_EMAIL=noreply@example.com\n'
                'OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED=false\n'
                'OPENHANDS_AGENT_COMPLETION_EMAIL_TEMPLATE_ID=0\n'
                'OPENHANDS_AGENT_COMPLETION_EMAIL_TO=\n'
                'OPENHANDS_AGENT_COMPLETION_EMAIL_SENDER_NAME=OpenHands Agent\n'
                'OPENHANDS_AGENT_COMPLETION_EMAIL_SENDER_EMAIL=noreply@example.com\n'
                'EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY=\n'
                'SLACK_WEBHOOK_URL_ERRORS_EMAIL=\n',
                encoding='utf-8',
            )

            fake_prompter = _FakePrompter(
                {
                    'Where are your tasks tracked': 'youtrack',
                    'Which platform hosts your source code': 'bitbucket',
                    'YouTrack base URL': 'https://youtrack.example',
                    'YouTrack token': 'yt-token',
                    'YouTrack assignee login': 'me',
                    'YouTrack project key': 'PROJ',
                    'YouTrack review state field': 'State',
                    'YouTrack review state value': 'In Review',
                    'YouTrack issue states to process': ['Todo', 'Open'],
                    'Repository id': 'client',
                    'Repository display name': 'Client',
                    'Local path to the checked-out repository': '.',
                    'Bitbucket API base URL': 'https://api.bitbucket.org/2.0',
                    'Bitbucket repository token': 'bb-token',
                    'Repository owner, workspace, or group': 'workspace',
                    'Repository name or slug': 'repo',
                    'Set an explicit destination branch': False,
                    'OpenHands base URL': 'http://localhost:3000',
                    'OpenHands API key': 'local',
                    'Maximum retries for external API calls': 5,
                    'State file path': 'openhands_agent_state.json',
                    'OpenHands LLM model': 'openai/gpt-4o',
                    'OpenHands LLM API key': 'llm-key',
                    'OpenHands LLM base URL': 'https://api.openai.com/v1',
                    'Enable failure notification emails': False,
                    'Enable completion notification emails': False,
                }
            )

            with patch('openhands_agent.configure_project.PromptAdapter', return_value=fake_prompter):
                exit_code = main(['--template', str(template_path), '--output', str(output_path)])

            self.assertEqual(exit_code, 0)
            written_env = _read_env_file(str(output_path))
            self.assertEqual(written_env['OPENHANDS_AGENT_ISSUE_PLATFORM'], 'youtrack')
            self.assertEqual(written_env['YOUTRACK_ISSUE_STATES'], 'Todo,Open')
            self.assertEqual(written_env['OPENHANDS_LLM_API_KEY'], 'llm-key')

    def test_main_returns_non_zero_when_configuration_is_still_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            template_path = temp_path / '.env.example'
            output_path = temp_path / '.env'
            template_path.write_text(
                'OPENHANDS_AGENT_ISSUE_PLATFORM=youtrack\n'
                'OPENHANDS_AGENT_TICKET_SYSTEM=youtrack\n'
                'YOUTRACK_BASE_URL=\n'
                'YOUTRACK_TOKEN=\n'
                'YOUTRACK_PROJECT=\n'
                'YOUTRACK_ASSIGNEE=\n'
                'YOUTRACK_REVIEW_STATE_FIELD=State\n'
                'YOUTRACK_REVIEW_STATE=In Review\n'
                'YOUTRACK_ISSUE_STATES=Todo,Open\n'
                'REPOSITORY_ID=\n'
                'REPOSITORY_DISPLAY_NAME=\n'
                'REPOSITORY_LOCAL_PATH=.\n'
                'REPOSITORY_BASE_URL=\n'
                'REPOSITORY_TOKEN=\n'
                'REPOSITORY_OWNER=\n'
                'REPOSITORY_REPO_SLUG=\n'
                'REPOSITORY_DESTINATION_BRANCH=\n'
                'OPENHANDS_BASE_URL=http://localhost:3000\n'
                'OPENHANDS_API_KEY=local\n'
                'OPENHANDS_AGENT_MAX_RETRIES=5\n'
                'OPENHANDS_AGENT_STATE_FILE=openhands_agent_state.json\n'
                'OPENHANDS_LLM_MODEL=\n'
                'OPENHANDS_LLM_API_KEY=\n'
                'OPENHANDS_LLM_BASE_URL=\n'
                'OPENHANDS_AGENT_FAILURE_EMAIL_ENABLED=false\n'
                'OPENHANDS_AGENT_FAILURE_EMAIL_TEMPLATE_ID=0\n'
                'OPENHANDS_AGENT_FAILURE_EMAIL_TO=\n'
                'OPENHANDS_AGENT_FAILURE_EMAIL_SENDER_NAME=OpenHands Agent\n'
                'OPENHANDS_AGENT_FAILURE_EMAIL_SENDER_EMAIL=noreply@example.com\n'
                'OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED=false\n'
                'OPENHANDS_AGENT_COMPLETION_EMAIL_TEMPLATE_ID=0\n'
                'OPENHANDS_AGENT_COMPLETION_EMAIL_TO=\n'
                'OPENHANDS_AGENT_COMPLETION_EMAIL_SENDER_NAME=OpenHands Agent\n'
                'OPENHANDS_AGENT_COMPLETION_EMAIL_SENDER_EMAIL=noreply@example.com\n'
                'EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY=\n'
                'SLACK_WEBHOOK_URL_ERRORS_EMAIL=\n',
                encoding='utf-8',
            )

            fake_prompter = _FakePrompter(
                {
                    'Where are your tasks tracked': 'youtrack',
                    'Which platform hosts your source code': 'bitbucket',
                    'YouTrack base URL': 'https://youtrack.example',
                    'YouTrack token': '',
                    'YouTrack assignee login': 'me',
                    'YouTrack project key': 'PROJ',
                    'YouTrack review state field': 'State',
                    'YouTrack review state value': 'In Review',
                    'YouTrack issue states to process': ['Todo', 'Open'],
                    'Repository id': 'client',
                    'Repository display name': 'Client',
                    'Local path to the checked-out repository': '.',
                    'Bitbucket API base URL': 'https://api.bitbucket.org/2.0',
                    'Bitbucket repository token': 'bb-token',
                    'Repository owner, workspace, or group': 'workspace',
                    'Repository name or slug': 'repo',
                    'Set an explicit destination branch': False,
                    'OpenHands base URL': 'http://localhost:3000',
                    'OpenHands API key': 'local',
                    'Maximum retries for external API calls': 5,
                    'State file path': 'openhands_agent_state.json',
                    'OpenHands LLM model': '',
                    'OpenHands LLM API key': '',
                    'OpenHands LLM base URL': '',
                    'Enable failure notification emails': False,
                    'Enable completion notification emails': False,
                }
            )

            with patch('openhands_agent.configure_project.PromptAdapter', return_value=fake_prompter):
                exit_code = main(['--template', str(template_path), '--output', str(output_path)])

            self.assertEqual(exit_code, 1)


if __name__ == '__main__':
    unittest.main()
