from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import kato.configure_project as configure_project
from kato.validate_env import _read_env_file, validate_agent_env, validate_openhands_env


class ConfigureProjectTests(unittest.TestCase):
    @staticmethod
    def _patch_prompts(responses: dict[str, object]):
        def _get(message: str):
            if message not in responses:
                raise AssertionError(f'unexpected prompt: {message}')
            return responses[message]

        return patch.multiple(
            configure_project,
            input_yes_no=Mock(side_effect=lambda message, default=True: bool(_get(message))),
            input_bool=Mock(side_effect=lambda message, default=True: bool(_get(message))),
            input_str=Mock(
                side_effect=lambda message, default=None, allow_empty=False: str(_get(message))
            ),
            input_int=Mock(side_effect=lambda message, default=None: int(_get(message))),
            input_enum=Mock(
                side_effect=lambda message, values, default=None: str(_get(message))
            ),
            input_list=Mock(
                side_effect=lambda message, default=None: list(_get(message))
            ),
        )

    def test_build_configuration_values_for_youtrack_and_github_repo(self) -> None:
        with self._patch_prompts(
            {
                'Where are your tasks tracked': 'youtrack',
                'YouTrack base URL': 'https://youtrack.example',
                'YouTrack token': 'yt-token',
                'YouTrack assignee login': 'developer',
                'YouTrack project key': 'PROJ',
                'YouTrack in-progress state field': 'State',
                'YouTrack in-progress state value': 'In Progress',
                'YouTrack review state field': 'State',
                'YouTrack review state value': 'In Review',
                'YouTrack issue states to process': ['Open', 'Ready for Dev'],
                'Scan a projects folder for checked-out repositories': False,
                'Projects root folder containing checked-out repositories': './client',
                'OpenHands base URL': 'http://localhost:3000',
                'OpenHands API key': 'local',
                'OpenHands secret key': 'openhands-secret',
                'OpenHands LLM model': 'openai/gpt-4o',
                'OpenHands LLM API key': 'llm-key',
                'OpenHands LLM base URL': 'https://api.openai.com/v1',
                'Skip testing before publishing pull requests': False,
                'Use a dedicated OpenHands testing container': False,
                'Enable failure notification emails': False,
                'Enable completion notification emails': False,
            }
        ):
            values = configure_project.build_configuration_values({})

        self.assertEqual(values['KATO_ISSUE_PLATFORM'], 'youtrack')
        self.assertEqual(values['KATO_TICKET_SYSTEM'], 'youtrack')
        self.assertEqual(values['YOUTRACK_ISSUE_STATES'], 'Open,Ready for Dev')
        self.assertEqual(values['REPOSITORY_ROOT_PATH'], str(Path('./client').resolve()))
        self.assertNotIn('OPENHANDS_SANDBOX_VOLUMES', values)
        self.assertEqual(values['OPENHANDS_LLM_API_KEY'], 'llm-key')
        self.assertEqual(values['OPENHANDS_SKIP_TESTING'], 'false')
        self.assertEqual(values['OPENHANDS_TESTING_CONTAINER_ENABLED'], 'false')
        self.assertEqual(values['OPENHANDS_MODEL_SMOKE_TEST_ENABLED'], 'true')
        self.assertEqual(values['OPENHANDS_TASK_SCAN_STARTUP_DELAY_SECONDS'], '30')
        self.assertEqual(values['OPENHANDS_TASK_SCAN_INTERVAL_SECONDS'], '60')
        self.assertEqual(values['OH_SECRET_KEY'], 'openhands-secret')
        self.assertEqual(self._validate_agent_env(values), [])
        self.assertEqual(validate_openhands_env(values), [])

    def test_build_configuration_values_for_openrouter_model(self) -> None:
        with self._patch_prompts(
            {
                'Where are your tasks tracked': 'youtrack',
                'YouTrack base URL': 'https://youtrack.example',
                'YouTrack token': 'yt-token',
                'YouTrack assignee login': 'developer',
                'YouTrack project key': 'PROJ',
                'YouTrack in-progress state field': 'State',
                'YouTrack in-progress state value': 'In Progress',
                'YouTrack review state field': 'State',
                'YouTrack review state value': 'In Review',
                'YouTrack issue states to process': ['Open'],
                'Scan a projects folder for checked-out repositories': False,
                'Projects root folder containing checked-out repositories': '.',
                'OpenHands base URL': 'http://localhost:3000',
                'OpenHands API key': 'local',
                'OpenHands secret key': 'openhands-secret',
                'OpenHands LLM model': 'openrouter/openai/gpt-4o-mini',
                'OpenHands LLM API key': 'router-key',
                'OpenHands LLM base URL': 'https://openrouter.ai/api/v1',
                'Skip testing before publishing pull requests': False,
                'Use a dedicated OpenHands testing container': False,
                'Enable failure notification emails': False,
                'Enable completion notification emails': False,
            }
        ):
            values = configure_project.build_configuration_values({})

        self.assertEqual(values['OPENHANDS_LLM_MODEL'], 'openrouter/openai/gpt-4o-mini')
        self.assertEqual(values['OPENHANDS_LLM_API_KEY'], 'router-key')
        self.assertEqual(values['OPENHANDS_LLM_BASE_URL'], 'https://openrouter.ai/api/v1')
        self.assertEqual(self._validate_agent_env(values), [])
        self.assertEqual(validate_openhands_env(values), [])

    def test_build_configuration_values_for_jira_and_bedrock(self) -> None:
        with self._patch_prompts(
            {
                'Where are your tasks tracked': 'jira',
                'Jira base URL': 'https://company.atlassian.net',
                'Jira token': 'jira-token',
                'Jira assignee account id or username': 'dev-user',
                'Jira project key': 'ENG',
                'Jira in-progress state field': 'status',
                'Jira in-progress state value': 'In Progress',
                'Jira review state field': 'status',
                'Jira review state value': 'Code Review',
                'Jira issue states to process': ['To Do', 'Selected for Development'],
                'Jira user email for basic auth': 'dev@example.com',
                'Scan a projects folder for checked-out repositories': False,
                'Projects root folder containing checked-out repositories': '.',
                'OpenHands base URL': 'http://localhost:3000',
                'OpenHands API key': 'local',
                'OpenHands secret key': 'openhands-secret',
                'OpenHands LLM model': 'bedrock/anthropic.claude-3-sonnet-20240229-v1:0',
                'How should OpenHands authenticate to Bedrock': 'bearer_token',
                'AWS bearer token for Bedrock': 'bedrock-token',
                'Skip testing before publishing pull requests': False,
                'Use a dedicated OpenHands testing container': False,
                'Enable failure notification emails': True,
                'Enable completion notification emails': False,
                'Email provider API key': 'sendinblue-key',
                'Slack webhook URL for email errors': '',
                'Failure email template id': 42,
                'Failure email recipient': 'ops@example.com',
                'Failure email sender name': 'Kato',
                'Failure email sender address': 'noreply@example.com',
            }
        ):
            values = configure_project.build_configuration_values({})

        self.assertEqual(values['JIRA_EMAIL'], 'dev@example.com')
        self.assertEqual(values['JIRA_ISSUE_STATES'], 'To Do,Selected for Development')
        self.assertEqual(values['AWS_BEARER_TOKEN_BEDROCK'], 'bedrock-token')
        self.assertEqual(values['OPENHANDS_LLM_API_KEY'], '')
        self.assertEqual(values['AWS_SESSION_TOKEN'], '')
        self.assertEqual(values['OPENHANDS_CONTAINER_LOG_ALL_EVENTS'], 'true')
        self.assertEqual(values['OPENHANDS_SKIP_TESTING'], 'false')
        self.assertEqual(values['OPENHANDS_TESTING_CONTAINER_ENABLED'], 'false')
        self.assertEqual(values['OPENHANDS_MODEL_SMOKE_TEST_ENABLED'], 'true')
        self.assertEqual(values['OPENHANDS_TASK_SCAN_STARTUP_DELAY_SECONDS'], '30')
        self.assertEqual(values['OPENHANDS_TASK_SCAN_INTERVAL_SECONDS'], '60')
        self.assertEqual(values['KATO_FAILURE_EMAIL_ENABLED'], 'true')
        self.assertEqual(values['OH_SECRET_KEY'], 'openhands-secret')
        self.assertEqual(self._validate_agent_env(values), [])
        self.assertEqual(validate_openhands_env(values), [])

    def test_build_configuration_values_for_bitbucket_includes_username(self) -> None:
        with self._patch_prompts(
            {
                'Where are your tasks tracked': 'bitbucket',
                'Bitbucket Issues base URL': 'https://api.bitbucket.org/2.0',
                'Bitbucket Issues token': 'bb-token',
                'Bitbucket Issues username for git auth': 'bb-user',
                'Bitbucket Issues email for pull request auth': 'bb-user@example.com',
                'Bitbucket Issues assignee username': 'reviewer',
                'Bitbucket Issues workspace': 'workspace',
                'Bitbucket Issues issues repository slug': 'repo',
                'Bitbucket Issues in-progress state field': 'state',
                'Bitbucket Issues in-progress state value': 'open',
                'Bitbucket Issues review state field': 'state',
                'Bitbucket Issues review state value': 'resolved',
                'Bitbucket Issues issue states to process': ['new', 'triaged'],
                'Scan a projects folder for checked-out repositories': False,
                'Projects root folder containing checked-out repositories': '.',
                'OpenHands base URL': 'http://localhost:3000',
                'OpenHands API key': 'local',
                'OpenHands secret key': 'openhands-secret',
                'OpenHands LLM model': 'openai/gpt-4o',
                'OpenHands LLM API key': 'llm-key',
                'OpenHands LLM base URL': 'https://api.openai.com/v1',
                'Skip testing before publishing pull requests': False,
                'Use a dedicated OpenHands testing container': False,
                'Enable failure notification emails': False,
                'Enable completion notification emails': False,
            }
        ):
            values = configure_project.build_configuration_values({})

        self.assertEqual(values['BITBUCKET_USERNAME'], 'bb-user')
        self.assertEqual(values['BITBUCKET_API_EMAIL'], 'bb-user@example.com')
        self.assertEqual(values['BITBUCKET_API_TOKEN'], 'bb-token')
        self.assertEqual(values['BITBUCKET_ISSUES_WORKSPACE'], 'workspace')
        self.assertEqual(values['BITBUCKET_ISSUES_REPO_SLUG'], 'repo')
        self.assertEqual(self._validate_agent_env(values), [])
        self.assertEqual(validate_openhands_env(values), [])

    def test_build_configuration_values_for_bedrock_access_keys_includes_session_token(self) -> None:
        with self._patch_prompts(
            {
                'Where are your tasks tracked': 'youtrack',
                'YouTrack base URL': 'https://youtrack.example',
                'YouTrack token': 'yt-token',
                'YouTrack assignee login': 'developer',
                'YouTrack project key': 'PROJ',
                'YouTrack in-progress state field': 'State',
                'YouTrack in-progress state value': 'In Progress',
                'YouTrack review state field': 'State',
                'YouTrack review state value': 'In Review',
                'YouTrack issue states to process': ['Open'],
                'Scan a projects folder for checked-out repositories': False,
                'Projects root folder containing checked-out repositories': '.',
                'OpenHands base URL': 'http://localhost:3000',
                'OpenHands API key': 'local',
                'OpenHands secret key': 'openhands-secret',
                'OpenHands LLM model': 'bedrock/anthropic.claude-3-sonnet-20240229-v1:0',
                'How should OpenHands authenticate to Bedrock': 'access_keys',
                'AWS access key id': 'aws-key',
                'AWS secret access key': 'aws-secret',
                'AWS region name': 'us-west-2',
                'AWS session token (optional)': 'aws-session-token',
                'Skip testing before publishing pull requests': False,
                'Use a dedicated OpenHands testing container': False,
                'Enable failure notification emails': False,
                'Enable completion notification emails': False,
            }
        ):
            values = configure_project.build_configuration_values({})

        self.assertEqual(values['AWS_ACCESS_KEY_ID'], 'aws-key')
        self.assertEqual(values['AWS_SECRET_ACCESS_KEY'], 'aws-secret')
        self.assertEqual(values['AWS_REGION_NAME'], 'us-west-2')
        self.assertEqual(values['AWS_SESSION_TOKEN'], 'aws-session-token')
        self.assertEqual(values['AWS_BEARER_TOKEN_BEDROCK'], '')
        self.assertEqual(values['OPENHANDS_LLM_API_KEY'], '')
        self.assertEqual(values['OPENHANDS_CONTAINER_LOG_ALL_EVENTS'], 'true')
        self.assertEqual(self._validate_agent_env(values), [])
        self.assertEqual(validate_openhands_env(values), [])

    def test_build_configuration_values_supports_dedicated_testing_container(self) -> None:
        with self._patch_prompts(
            {
                'Where are your tasks tracked': 'youtrack',
                'YouTrack base URL': 'https://youtrack.example',
                'YouTrack token': 'yt-token',
                'YouTrack assignee login': 'developer',
                'YouTrack project key': 'PROJ',
                'YouTrack in-progress state field': 'State',
                'YouTrack in-progress state value': 'In Progress',
                'YouTrack review state field': 'State',
                'YouTrack review state value': 'In Review',
                'YouTrack issue states to process': ['Open'],
                'Scan a projects folder for checked-out repositories': False,
                'Projects root folder containing checked-out repositories': './client',
                'OpenHands base URL': 'http://localhost:3000',
                'OpenHands API key': 'local',
                'OpenHands secret key': 'openhands-secret',
                'OpenHands LLM model': 'openai/gpt-4o',
                'OpenHands LLM API key': 'llm-key',
                'OpenHands LLM base URL': 'https://api.openai.com/v1',
                'Skip testing before publishing pull requests': False,
                'Use a dedicated OpenHands testing container': True,
                'OpenHands testing base URL': 'http://localhost:3001',
                'OpenHands testing LLM model': 'openai/gpt-4o-mini',
                'OpenHands testing LLM API key': 'testing-key',
                'OpenHands testing LLM base URL': '',
                'Enable failure notification emails': False,
                'Enable completion notification emails': False,
            }
        ):
            values = configure_project.build_configuration_values({})

        self.assertEqual(values['OPENHANDS_TESTING_CONTAINER_ENABLED'], 'true')
        self.assertEqual(values['OPENHANDS_SKIP_TESTING'], 'false')
        self.assertEqual(values['OPENHANDS_TESTING_BASE_URL'], 'http://localhost:3001')
        self.assertEqual(values['OPENHANDS_TESTING_LLM_MODEL'], 'openai/gpt-4o-mini')
        self.assertEqual(values['OPENHANDS_TESTING_LLM_API_KEY'], 'testing-key')
        self.assertEqual(values['OPENHANDS_TESTING_LLM_BASE_URL'], '')
        self.assertEqual(values['OPENHANDS_MODEL_SMOKE_TEST_ENABLED'], 'true')
        self.assertEqual(values['OPENHANDS_TASK_SCAN_STARTUP_DELAY_SECONDS'], '30')
        self.assertEqual(values['OPENHANDS_TASK_SCAN_INTERVAL_SECONDS'], '60')
        self.assertEqual(values['OPENHANDS_CONTAINER_LOG_ALL_EVENTS'], 'true')
        self.assertEqual(validate_openhands_env(values), [])

    def test_prompt_repository_discovers_checked_out_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir)
            client_repo = projects_root / 'client'
            backend_repo = projects_root / 'backend'
            self._create_git_repository(
                client_repo,
                'git@github.com:acme/client.git',
            )
            self._create_git_repository(
                backend_repo,
                'git@github.com:acme/backend.git',
            )

            with self._patch_prompts(
                {
                    'Scan a projects folder for checked-out repositories': True,
                    'Projects folder to scan for repositories': str(projects_root),
                    'Projects root folder containing checked-out repositories': str(projects_root),
                }
            ):
                values = configure_project._prompt_repository({})

            self.assertEqual(values['REPOSITORY_ROOT_PATH'], str(projects_root.resolve()))
            self.assertNotIn('OPENHANDS_SANDBOX_VOLUMES', values)

    def test_prompt_repository_raises_when_scanned_folder_has_no_git_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self._patch_prompts(
                {
                    'Scan a projects folder for checked-out repositories': True,
                    'Projects folder to scan for repositories': temp_dir,
                }
            ):
                with self.assertRaisesRegex(ValueError, 'no git repositories were found under'):
                    configure_project._prompt_repository({})

    def test_read_git_remote_url_tolerates_duplicate_git_config_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository_path = Path(temp_dir) / 'task-core-lib'
            git_dir = repository_path / '.git'
            git_dir.mkdir(parents=True)
            (git_dir / 'config').write_text(
                '[remote "origin"]\n'
                '\turl = git@github.com:acme/task-core-lib.git\n'
                '[branch "master"]\n'
                '\tvscode-merge-base = origin/master\n'
                '\tvscode-merge-base = origin/main\n',
                encoding='utf-8',
            )

            result = configure_project._read_git_remote_url(repository_path)

            self.assertEqual(result, 'git@github.com:acme/task-core-lib.git')

    def test_render_env_text_replaces_existing_keys(self) -> None:
        rendered = configure_project.render_env_text(
            '# heading\nKATO_ISSUE_PLATFORM=youtrack\nYOUTRACK_ISSUE_STATES=Todo,Open\n',
            {
                'KATO_ISSUE_PLATFORM': 'jira',
                'YOUTRACK_ISSUE_STATES': 'Open,Ready for Dev',
                'JIRA_ISSUE_STATES': 'To Do,Open',
            },
        )

        self.assertIn('KATO_ISSUE_PLATFORM=jira', rendered)
        self.assertIn("YOUTRACK_ISSUE_STATES='Open,Ready for Dev'", rendered)
        self.assertIn("JIRA_ISSUE_STATES='To Do,Open'", rendered)

    def test_render_env_text_quotes_values_with_spaces(self) -> None:
        rendered = configure_project.render_env_text(
            'YOUTRACK_REVIEW_STATE=In Review\n'
            'KATO_COMPLETION_EMAIL_SENDER_NAME=Kato\n',
            {
                'YOUTRACK_REVIEW_STATE': 'To Verify',
                'KATO_COMPLETION_EMAIL_SENDER_NAME': 'Kato',
            },
        )

        self.assertIn("YOUTRACK_REVIEW_STATE='To Verify'", rendered)
        self.assertIn('KATO_COMPLETION_EMAIL_SENDER_NAME=Kato', rendered)

    @staticmethod
    def _validate_agent_env(values: dict[str, str]) -> list[str]:
        with patch('kato.validate_env.discover_git_repositories', return_value=[]):
            return validate_agent_env(values)

    def test_main_writes_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            template_path = temp_path / '.env.example'
            output_path = temp_path / '.env'
            template_path.write_text(
                'KATO_ISSUE_PLATFORM=youtrack\n'
                'KATO_TICKET_SYSTEM=youtrack\n'
                'YOUTRACK_BASE_URL=\n'
                'YOUTRACK_TOKEN=\n'
                'YOUTRACK_PROJECT=\n'
                'YOUTRACK_ASSIGNEE=\n'
                'YOUTRACK_PROGRESS_STATE_FIELD=State\n'
                'YOUTRACK_PROGRESS_STATE=In Progress\n'
                'YOUTRACK_REVIEW_STATE_FIELD=State\n'
                'YOUTRACK_REVIEW_STATE=In Review\n'
                'YOUTRACK_ISSUE_STATES=Todo,Open\n'
                'REPOSITORY_ROOT_PATH=.\n'
                'OPENHANDS_BASE_URL=http://localhost:3000\n'
                'OPENHANDS_API_KEY=local\n'
                'OPENHANDS_SKIP_TESTING=false\n'
                'OPENHANDS_TESTING_CONTAINER_ENABLED=false\n'
                'OPENHANDS_TESTING_BASE_URL=http://localhost:3001\n'
                'OH_SECRET_KEY=\n'
                'OPENHANDS_LLM_MODEL=\n'
                'OPENHANDS_LLM_API_KEY=\n'
                'OPENHANDS_LLM_BASE_URL=\n'
                'OPENHANDS_TESTING_LLM_MODEL=\n'
                'OPENHANDS_TESTING_LLM_API_KEY=\n'
                'OPENHANDS_TESTING_LLM_BASE_URL=\n'
                'OPENHANDS_CONTAINER_LOG_ALL_EVENTS=true\n'
                'AWS_ACCESS_KEY_ID=\n'
                'AWS_SECRET_ACCESS_KEY=\n'
                'AWS_REGION_NAME=\n'
                'AWS_SESSION_TOKEN=\n'
                'AWS_BEARER_TOKEN_BEDROCK=\n'
                'KATO_FAILURE_EMAIL_ENABLED=false\n'
                'KATO_FAILURE_EMAIL_TEMPLATE_ID=0\n'
                'KATO_FAILURE_EMAIL_TO=\n'
                'KATO_FAILURE_EMAIL_SENDER_NAME=Kato\n'
                'KATO_FAILURE_EMAIL_SENDER_EMAIL=noreply@example.com\n'
                'KATO_COMPLETION_EMAIL_ENABLED=false\n'
                'KATO_COMPLETION_EMAIL_TEMPLATE_ID=0\n'
                'KATO_COMPLETION_EMAIL_TO=\n'
                'KATO_COMPLETION_EMAIL_SENDER_NAME=Kato\n'
                'KATO_COMPLETION_EMAIL_SENDER_EMAIL=noreply@example.com\n'
                'EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY=\n'
                'SLACK_WEBHOOK_URL_ERRORS_EMAIL=\n',
                encoding='utf-8',
            )

            responses = {
                'Where are your tasks tracked': 'youtrack',
                'YouTrack base URL': 'https://youtrack.example',
                'YouTrack token': 'yt-token',
                'YouTrack assignee login': 'me',
                'YouTrack project key': 'PROJ',
                'YouTrack in-progress state field': 'State',
                'YouTrack in-progress state value': 'In Progress',
                'YouTrack review state field': 'State',
                'YouTrack review state value': 'In Review',
                'YouTrack issue states to process': ['Todo', 'Open'],
                'Scan a projects folder for checked-out repositories': False,
                'Projects root folder containing checked-out repositories': '.',
                'OpenHands base URL': 'http://localhost:3000',
                'OpenHands API key': 'local',
                'OpenHands secret key': 'openhands-secret',
                'OpenHands LLM model': 'openai/gpt-4o',
                'OpenHands LLM API key': 'llm-key',
                'OpenHands LLM base URL': 'https://api.openai.com/v1',
                'Skip testing before publishing pull requests': False,
                'Use a dedicated OpenHands testing container': False,
                'Enable failure notification emails': False,
                'Enable completion notification emails': False,
            }

            with self._patch_prompts(responses):
                exit_code = configure_project.main(
                    [
                        '--template',
                        str(template_path),
                        '--output',
                        str(output_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            written_env = _read_env_file(str(output_path))
            self.assertEqual(written_env['KATO_ISSUE_PLATFORM'], 'youtrack')
            self.assertEqual(written_env['YOUTRACK_ISSUE_STATES'], 'Todo,Open')
            self.assertEqual(written_env['OH_SECRET_KEY'], 'openhands-secret')
            self.assertEqual(written_env['OPENHANDS_LLM_API_KEY'], 'llm-key')
            self.assertEqual(written_env['OPENHANDS_SKIP_TESTING'], 'false')
            self.assertEqual(written_env['OPENHANDS_TESTING_CONTAINER_ENABLED'], 'false')
            self.assertEqual(written_env['OPENHANDS_CONTAINER_LOG_ALL_EVENTS'], 'true')

    def test_main_returns_zero_when_configuration_is_still_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            template_path = temp_path / '.env.example'
            output_path = temp_path / '.env'
            template_path.write_text(
                'KATO_ISSUE_PLATFORM=youtrack\n'
                'KATO_TICKET_SYSTEM=youtrack\n'
                'YOUTRACK_BASE_URL=\n'
                'YOUTRACK_TOKEN=\n'
                'YOUTRACK_PROJECT=\n'
                'YOUTRACK_ASSIGNEE=\n'
                'YOUTRACK_PROGRESS_STATE_FIELD=State\n'
                'YOUTRACK_PROGRESS_STATE=In Progress\n'
                'YOUTRACK_REVIEW_STATE_FIELD=State\n'
                'YOUTRACK_REVIEW_STATE=In Review\n'
                'YOUTRACK_ISSUE_STATES=Todo,Open\n'
                'REPOSITORY_ROOT_PATH=.\n'
                'OPENHANDS_BASE_URL=http://localhost:3000\n'
                'OPENHANDS_API_KEY=local\n'
                'OPENHANDS_SKIP_TESTING=false\n'
                'OPENHANDS_TESTING_CONTAINER_ENABLED=false\n'
                'OPENHANDS_TESTING_BASE_URL=http://localhost:3001\n'
                'OH_SECRET_KEY=\n'
                'OPENHANDS_LLM_MODEL=\n'
                'OPENHANDS_LLM_API_KEY=\n'
                'OPENHANDS_LLM_BASE_URL=\n'
                'OPENHANDS_TESTING_LLM_MODEL=\n'
                'OPENHANDS_TESTING_LLM_API_KEY=\n'
                'OPENHANDS_TESTING_LLM_BASE_URL=\n'
                'OPENHANDS_CONTAINER_LOG_ALL_EVENTS=true\n'
                'AWS_ACCESS_KEY_ID=\n'
                'AWS_SECRET_ACCESS_KEY=\n'
                'AWS_REGION_NAME=\n'
                'AWS_SESSION_TOKEN=\n'
                'AWS_BEARER_TOKEN_BEDROCK=\n'
                'KATO_FAILURE_EMAIL_ENABLED=false\n'
                'KATO_FAILURE_EMAIL_TEMPLATE_ID=0\n'
                'KATO_FAILURE_EMAIL_TO=\n'
                'KATO_FAILURE_EMAIL_SENDER_NAME=Kato\n'
                'KATO_FAILURE_EMAIL_SENDER_EMAIL=noreply@example.com\n'
                'KATO_COMPLETION_EMAIL_ENABLED=false\n'
                'KATO_COMPLETION_EMAIL_TEMPLATE_ID=0\n'
                'KATO_COMPLETION_EMAIL_TO=\n'
                'KATO_COMPLETION_EMAIL_SENDER_NAME=Kato\n'
                'KATO_COMPLETION_EMAIL_SENDER_EMAIL=noreply@example.com\n'
                'EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY=\n'
                'SLACK_WEBHOOK_URL_ERRORS_EMAIL=\n',
                encoding='utf-8',
            )

            responses = {
                'Where are your tasks tracked': 'youtrack',
                'YouTrack base URL': 'https://youtrack.example',
                'YouTrack token': '',
                'YouTrack assignee login': 'me',
                'YouTrack project key': 'PROJ',
                'YouTrack in-progress state field': 'State',
                'YouTrack in-progress state value': 'In Progress',
                'YouTrack review state field': 'State',
                'YouTrack review state value': 'In Review',
                'YouTrack issue states to process': ['Todo', 'Open'],
                'Scan a projects folder for checked-out repositories': False,
                'Projects root folder containing checked-out repositories': '.',
                'OpenHands base URL': 'http://localhost:3000',
                'OpenHands API key': 'local',
                'OpenHands secret key': 'openhands-secret',
                'OpenHands LLM model': '',
                'OpenHands LLM API key': '',
                'OpenHands LLM base URL': '',
                'Skip testing before publishing pull requests': False,
                'Use a dedicated OpenHands testing container': False,
                'Enable failure notification emails': False,
                'Enable completion notification emails': False,
            }

            with self._patch_prompts(responses):
                exit_code = configure_project.main(
                    [
                        '--template',
                        str(template_path),
                        '--output',
                        str(output_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())

    @staticmethod
    def _create_git_repository(path: Path, remote_url: str) -> None:
        git_dir = path / '.git'
        git_dir.mkdir(parents=True)
        (git_dir / 'config').write_text(
            '[core]\n'
            '\trepositoryformatversion = 0\n'
            '[remote "origin"]\n'
            f'\turl = {remote_url}\n',
            encoding='utf-8',
        )


if __name__ == '__main__':
    unittest.main()
