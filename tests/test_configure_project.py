from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import openhands_agent.configure_project as configure_project
from openhands_agent.validate_env import _read_env_file, validate_agent_env, validate_openhands_env


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
                'Which platform hosts your source code': 'github',
                'YouTrack base URL': 'https://youtrack.example',
                'YouTrack token': 'yt-token',
                'YouTrack assignee login': 'developer',
                'YouTrack project key': 'PROJ',
                'YouTrack review state field': 'State',
                'YouTrack review state value': 'In Review',
                'YouTrack issue states to process': ['Open', 'Ready for Dev'],
                'Scan a projects folder for checked-out repositories': False,
                'Repository id': 'client',
                'Repository display name': 'Client',
                'Local path to the checked-out repository': './client',
                'Github API base URL': 'https://api.github.com',
                'Github repository token': 'gh-token',
                'Repository owner, workspace, or group': 'shay-te',
                'Repository name or slug': 'open-hands-agent',
                'Set an explicit destination branch': True,
                'Destination branch': 'main',
                'Additional checked-out repository folders to grant OpenHands access': [],
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
        ):
            values = configure_project.build_configuration_values({})

        self.assertEqual(values['OPENHANDS_AGENT_ISSUE_PLATFORM'], 'youtrack')
        self.assertEqual(values['OPENHANDS_AGENT_TICKET_SYSTEM'], 'youtrack')
        self.assertEqual(values['YOUTRACK_ISSUE_STATES'], 'Open,Ready for Dev')
        self.assertEqual(values['REPOSITORY_BASE_URL'], 'https://api.github.com')
        self.assertEqual(values['REPOSITORY_DESTINATION_BRANCH'], 'main')
        self.assertIn(
            f"{Path('./client').resolve()}:{Path('./client').resolve()}:rw",
            values['OPENHANDS_SANDBOX_VOLUMES'],
        )
        self.assertEqual(values['OPENHANDS_LLM_API_KEY'], 'llm-key')
        self.assertEqual(validate_agent_env(values), [])
        self.assertEqual(validate_openhands_env(values), [])

    def test_build_configuration_values_for_jira_and_bedrock(self) -> None:
        with self._patch_prompts(
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
                'Scan a projects folder for checked-out repositories': False,
                'Repository id': 'backend',
                'Repository display name': 'Backend',
                'Local path to the checked-out repository': '.',
                'Bitbucket API base URL': 'https://api.bitbucket.org/2.0',
                'Bitbucket repository token': 'bb-token',
                'Repository owner, workspace, or group': 'workspace',
                'Repository name or slug': 'backend',
                'Set an explicit destination branch': False,
                'Additional checked-out repository folders to grant OpenHands access': [],
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
        ):
            values = configure_project.build_configuration_values({})

        self.assertEqual(values['JIRA_EMAIL'], 'dev@example.com')
        self.assertEqual(values['JIRA_ISSUE_STATES'], 'To Do,Selected for Development')
        self.assertEqual(values['AWS_BEARER_TOKEN_BEDROCK'], 'bedrock-token')
        self.assertEqual(values['OPENHANDS_LLM_API_KEY'], '')
        self.assertEqual(values['OPENHANDS_AGENT_FAILURE_EMAIL_ENABLED'], 'true')
        self.assertEqual(validate_agent_env(values), [])
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
                    'Repository numbers to grant OpenHands access': '1, 2',
                    'Additional repository numbers to grant OpenHands access (comma-separated, optional)': '1, 2',
                    'Github API base URL': 'https://api.github.com',
                    'Github repository token': 'gh-token',
                    'Repository owner, workspace, or group': 'acme',
                    'Repository name or slug': 'backend',
                    'Set an explicit destination branch': False,
                }
            ):
                values = configure_project._prompt_repository({}, 'github')

            self.assertEqual(values['REPOSITORY_LOCAL_PATH'], str(backend_repo.resolve()))
            self.assertEqual(values['REPOSITORY_ID'], 'backend')
            self.assertEqual(values['REPOSITORY_DISPLAY_NAME'], 'Backend')
            self.assertEqual(
                values['OPENHANDS_SANDBOX_VOLUMES'],
                ','.join(
                    [
                        f'{backend_repo.resolve()}:{backend_repo.resolve()}:rw',
                        f'{client_repo.resolve()}:{client_repo.resolve()}:rw',
                    ]
                ),
            )

    def test_prompt_repository_raises_when_scanned_folder_has_no_git_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self._patch_prompts(
                {
                    'Scan a projects folder for checked-out repositories': True,
                    'Projects folder to scan for repositories': temp_dir,
                }
            ):
                with self.assertRaisesRegex(ValueError, 'no git repositories were found under'):
                    configure_project._prompt_repository({}, 'github')

    def test_parse_repository_numbers_supports_spaces(self) -> None:
        numbers = configure_project._parse_repository_numbers('1, 3 ,4,5, 10', 10)

        self.assertEqual(numbers, [1, 3, 4, 5, 10])

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

    def test_render_selected_repository_compose_override_mounts_selected_paths_read_only(self) -> None:
        rendered = configure_project.render_selected_repository_compose_override(
            ['/tmp/client', '/tmp/backend']
        )

        self.assertIn(
            f"'{Path('/tmp/client').resolve()}:{Path('/tmp/client').resolve()}:ro'",
            rendered,
        )
        self.assertIn(
            f"'{Path('/tmp/backend').resolve()}:{Path('/tmp/backend').resolve()}:ro'",
            rendered,
        )

    def test_render_env_text_replaces_existing_keys(self) -> None:
        rendered = configure_project.render_env_text(
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
            compose_override_path = temp_path / '.docker-compose.selected-repos.yml'
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

            responses = {
                'Where are your tasks tracked': 'youtrack',
                'Which platform hosts your source code': 'bitbucket',
                'YouTrack base URL': 'https://youtrack.example',
                'YouTrack token': 'yt-token',
                'YouTrack assignee login': 'me',
                'YouTrack project key': 'PROJ',
                'YouTrack review state field': 'State',
                'YouTrack review state value': 'In Review',
                'YouTrack issue states to process': ['Todo', 'Open'],
                'Scan a projects folder for checked-out repositories': False,
                'Repository id': 'client',
                'Repository display name': 'Client',
                'Local path to the checked-out repository': '.',
                'Bitbucket API base URL': 'https://api.bitbucket.org/2.0',
                'Bitbucket repository token': 'bb-token',
                'Repository owner, workspace, or group': 'workspace',
                'Repository name or slug': 'repo',
                'Set an explicit destination branch': False,
                'Additional checked-out repository folders to grant OpenHands access': [],
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

            with self._patch_prompts(responses):
                exit_code = configure_project.main(
                    [
                        '--template',
                        str(template_path),
                        '--output',
                        str(output_path),
                        '--compose-override-output',
                        str(compose_override_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            written_env = _read_env_file(str(output_path))
            self.assertEqual(written_env['OPENHANDS_AGENT_ISSUE_PLATFORM'], 'youtrack')
            self.assertEqual(written_env['YOUTRACK_ISSUE_STATES'], 'Todo,Open')
            self.assertEqual(written_env['OPENHANDS_LLM_API_KEY'], 'llm-key')
            self.assertTrue(compose_override_path.exists())
            self.assertIn(':ro', compose_override_path.read_text(encoding='utf-8'))

    def test_main_returns_non_zero_when_configuration_is_still_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            template_path = temp_path / '.env.example'
            output_path = temp_path / '.env'
            compose_override_path = temp_path / '.docker-compose.selected-repos.yml'
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

            responses = {
                'Where are your tasks tracked': 'youtrack',
                'Which platform hosts your source code': 'bitbucket',
                'YouTrack base URL': 'https://youtrack.example',
                'YouTrack token': '',
                'YouTrack assignee login': 'me',
                'YouTrack project key': 'PROJ',
                'YouTrack review state field': 'State',
                'YouTrack review state value': 'In Review',
                'YouTrack issue states to process': ['Todo', 'Open'],
                'Scan a projects folder for checked-out repositories': False,
                'Repository id': 'client',
                'Repository display name': 'Client',
                'Local path to the checked-out repository': '.',
                'Bitbucket API base URL': 'https://api.bitbucket.org/2.0',
                'Bitbucket repository token': 'bb-token',
                'Repository owner, workspace, or group': 'workspace',
                'Repository name or slug': 'repo',
                'Set an explicit destination branch': False,
                'Additional checked-out repository folders to grant OpenHands access': [],
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

            with self._patch_prompts(responses):
                exit_code = configure_project.main(
                    [
                        '--template',
                        str(template_path),
                        '--output',
                        str(output_path),
                        '--compose-override-output',
                        str(compose_override_path),
                    ]
                )

            self.assertEqual(exit_code, 1)

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
