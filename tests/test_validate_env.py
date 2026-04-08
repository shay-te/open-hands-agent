import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from kato.validate_env import (
    validate_agent_env,
    validate_openhands_env,
)


class ValidateEnvTests(unittest.TestCase):
    def test_validate_agent_env_accepts_complete_configuration(self) -> None:
        errors = self._validate_agent_env(
            {
                'YOUTRACK_BASE_URL': 'https://youtrack.example',
                'YOUTRACK_TOKEN': 'yt-token',
                'YOUTRACK_PROJECT': 'PROJ',
                'YOUTRACK_ASSIGNEE': 'developer',
                'REPOSITORY_ROOT_PATH': '.',
                'OPENHANDS_BASE_URL': 'http://localhost:3000',
                'OPENHANDS_API_KEY': 'local',
            }
        )

        self.assertEqual(errors, [])

    def test_validate_agent_env_requires_email_fields_when_enabled(self) -> None:
        errors = self._validate_agent_env(
            {
                'YOUTRACK_BASE_URL': 'https://youtrack.example',
                'YOUTRACK_TOKEN': 'yt-token',
                'YOUTRACK_PROJECT': 'PROJ',
                'YOUTRACK_ASSIGNEE': 'developer',
                'REPOSITORY_ROOT_PATH': '.',
                'OPENHANDS_BASE_URL': 'http://localhost:3000',
                'OPENHANDS_API_KEY': 'local',
                'KATO_FAILURE_EMAIL_ENABLED': 'true',
            }
        )

        self.assertIn(
            'failure email is enabled but KATO_FAILURE_EMAIL_TEMPLATE_ID is missing',
            errors,
        )
        self.assertIn(
            'failure email is enabled but KATO_FAILURE_EMAIL_TO is missing',
            errors,
        )

    def test_validate_agent_env_requires_youtrack_assignee(self) -> None:
        errors = self._validate_agent_env(
            {
                'YOUTRACK_BASE_URL': 'https://youtrack.example',
                'YOUTRACK_TOKEN': 'yt-token',
                'YOUTRACK_PROJECT': 'PROJ',
                'REPOSITORY_ROOT_PATH': '.',
                'OPENHANDS_BASE_URL': 'http://localhost:3000',
                'OPENHANDS_API_KEY': 'local',
            }
        )

        self.assertIn('missing required agent env var: YOUTRACK_ASSIGNEE', errors)

    def test_validate_agent_env_accepts_jira_configuration(self) -> None:
        errors = self._validate_agent_env(
            {
                'KATO_ISSUE_PLATFORM': 'jira',
                'JIRA_BASE_URL': 'https://jira.example',
                'JIRA_TOKEN': 'jira-token',
                'JIRA_PROJECT': 'PROJ',
                'JIRA_ASSIGNEE': 'developer',
                'REPOSITORY_ROOT_PATH': '.',
                'OPENHANDS_BASE_URL': 'http://localhost:3000',
                'OPENHANDS_API_KEY': 'local',
            }
        )

        self.assertEqual(errors, [])

    def test_validate_agent_env_accepts_github_issues_configuration(self) -> None:
        errors = self._validate_agent_env(
            {
                'KATO_ISSUE_PLATFORM': 'github',
                'GITHUB_ISSUES_BASE_URL': 'https://api.github.com',
                'GITHUB_API_TOKEN': 'gh-token',
                'GITHUB_ISSUES_OWNER': 'workspace',
                'GITHUB_ISSUES_REPO': 'repo',
                'GITHUB_ISSUES_ASSIGNEE': 'octocat',
                'REPOSITORY_ROOT_PATH': '.',
                'OPENHANDS_BASE_URL': 'http://localhost:3000',
                'OPENHANDS_API_KEY': 'local',
            }
        )

        self.assertEqual(errors, [])

    def test_validate_agent_env_accepts_gitlab_issues_configuration(self) -> None:
        errors = self._validate_agent_env(
            {
                'KATO_ISSUE_PLATFORM': 'gitlab',
                'GITLAB_ISSUES_BASE_URL': 'https://gitlab.example/api/v4',
                'GITLAB_API_TOKEN': 'gl-token',
                'GITLAB_ISSUES_PROJECT': 'group/repo',
                'GITLAB_ISSUES_ASSIGNEE': 'developer',
                'REPOSITORY_ROOT_PATH': '.',
                'OPENHANDS_BASE_URL': 'http://localhost:3000',
                'OPENHANDS_API_KEY': 'local',
            }
        )

        self.assertEqual(errors, [])

    def test_validate_agent_env_accepts_bitbucket_issues_configuration(self) -> None:
        errors = self._validate_agent_env(
            {
                'KATO_ISSUE_PLATFORM': 'bitbucket',
                'BITBUCKET_ISSUES_BASE_URL': 'https://api.bitbucket.org/2.0',
                'BITBUCKET_API_TOKEN': 'bb-token',
                'BITBUCKET_USERNAME': 'bb-user',
                'BITBUCKET_API_EMAIL': 'bb-user@example.com',
                'BITBUCKET_ISSUES_WORKSPACE': 'workspace',
                'BITBUCKET_ISSUES_REPO_SLUG': 'repo',
                'BITBUCKET_ISSUES_ASSIGNEE': 'reviewer',
                'REPOSITORY_ROOT_PATH': '.',
                'OPENHANDS_BASE_URL': 'http://localhost:3000',
                'OPENHANDS_API_KEY': 'local',
            }
        )

        self.assertEqual(errors, [])

    def test_validate_agent_env_requires_provider_token_for_discovered_bitbucket_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / 'project'
            self._create_git_repository(
                repo_path,
                'git@bitbucket.org:workspace/project.git',
            )

            errors = validate_agent_env(
                {
                    'YOUTRACK_BASE_URL': 'https://youtrack.example',
                    'YOUTRACK_TOKEN': 'yt-token',
                    'YOUTRACK_PROJECT': 'PROJ',
                    'YOUTRACK_ASSIGNEE': 'developer',
                    'REPOSITORY_ROOT_PATH': str(repo_path),
                    'OPENHANDS_BASE_URL': 'http://localhost:3000',
                    'OPENHANDS_API_KEY': 'local',
                }
            )

        self.assertIn(
            'missing required repository provider env var: BITBUCKET_API_TOKEN',
            errors,
        )
        self.assertIn(
            'missing required repository provider env var: BITBUCKET_API_EMAIL',
            errors,
        )

    def test_validate_agent_env_accepts_provider_token_for_discovered_bitbucket_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir) / 'project'
            self._create_git_repository(
                repo_path,
                'git@bitbucket.org:workspace/project.git',
            )

            errors = validate_agent_env(
                {
                    'YOUTRACK_BASE_URL': 'https://youtrack.example',
                    'YOUTRACK_TOKEN': 'yt-token',
                    'YOUTRACK_PROJECT': 'PROJ',
                    'YOUTRACK_ASSIGNEE': 'developer',
                    'REPOSITORY_ROOT_PATH': str(repo_path),
                    'OPENHANDS_BASE_URL': 'http://localhost:3000',
                    'OPENHANDS_API_KEY': 'local',
                    'BITBUCKET_API_TOKEN': 'bb-token',
                    'BITBUCKET_USERNAME': 'bb-user',
                    'BITBUCKET_API_EMAIL': 'bb-user@example.com',
                }
            )

        self.assertEqual(errors, [])

    def test_validate_agent_env_requires_github_api_token(self) -> None:
        errors = self._validate_agent_env(
            {
                'KATO_ISSUE_PLATFORM': 'github',
                'GITHUB_ISSUES_BASE_URL': 'https://api.github.com',
                'GITHUB_ISSUES_OWNER': 'workspace',
                'GITHUB_ISSUES_REPO': 'repo',
                'GITHUB_ISSUES_ASSIGNEE': 'octocat',
                'REPOSITORY_ROOT_PATH': '.',
                'OPENHANDS_BASE_URL': 'http://localhost:3000',
                'OPENHANDS_API_KEY': 'local',
            }
        )

        self.assertIn('missing required agent env var: GITHUB_API_TOKEN', errors)

    def test_validate_agent_env_rejects_progress_and_review_states_in_issue_queue(self) -> None:
        errors = self._validate_agent_env(
            {
                'YOUTRACK_BASE_URL': 'https://youtrack.example',
                'YOUTRACK_TOKEN': 'yt-token',
                'YOUTRACK_PROJECT': 'PROJ',
                'YOUTRACK_ASSIGNEE': 'developer',
                'YOUTRACK_PROGRESS_STATE': 'In Progress',
                'YOUTRACK_REVIEW_STATE': 'To Verify',
                'YOUTRACK_ISSUE_STATES': 'Open,In Progress,To Verify',
                'REPOSITORY_ROOT_PATH': '.',
                'OPENHANDS_BASE_URL': 'http://localhost:3000',
                'OPENHANDS_API_KEY': 'local',
            }
        )

        self.assertIn(
            'YOUTRACK_ISSUE_STATES must not include progress state "In Progress" or review state "To Verify"',
            errors,
        )

    def test_validate_agent_env_requires_gitlab_api_token(self) -> None:
        errors = self._validate_agent_env(
            {
                'KATO_ISSUE_PLATFORM': 'gitlab',
                'GITLAB_ISSUES_BASE_URL': 'https://gitlab.example/api/v4',
                'GITLAB_ISSUES_PROJECT': 'group/repo',
                'GITLAB_ISSUES_ASSIGNEE': 'developer',
                'REPOSITORY_ROOT_PATH': '.',
                'OPENHANDS_BASE_URL': 'http://localhost:3000',
                'OPENHANDS_API_KEY': 'local',
            }
        )

        self.assertIn('missing required agent env var: GITLAB_API_TOKEN', errors)

    def test_validate_agent_env_rejects_unknown_issue_platform(self) -> None:
        errors = self._validate_agent_env({'KATO_ISSUE_PLATFORM': 'linear'})

        self.assertIn('unsupported issue platform: linear', errors)

    def test_validate_openhands_env_requires_api_key_for_non_bedrock_models(self) -> None:
        errors = validate_openhands_env(
            {
                'OH_SECRET_KEY': 'secret-key',
                'OPENHANDS_LLM_MODEL': 'openai/gpt-4o',
            }
        )

        self.assertEqual(errors, ['openai/gpt-4o requires OPENHANDS_LLM_API_KEY'])

    def test_validate_openhands_env_requires_base_url_for_openrouter_models(self) -> None:
        errors = validate_openhands_env(
            {
                'OH_SECRET_KEY': 'secret-key',
                'OPENHANDS_LLM_MODEL': 'openrouter/openai/gpt-4o-mini',
                'OPENHANDS_LLM_API_KEY': 'router-key',
            }
        )

        self.assertEqual(
            errors,
            ['openrouter/openai/gpt-4o-mini requires OPENHANDS_LLM_BASE_URL'],
        )

    def test_validate_openhands_env_skips_testing_container_validation_when_testing_is_disabled(self) -> None:
        errors = validate_openhands_env(
            {
                'OH_SECRET_KEY': 'secret-key',
                'OPENHANDS_LLM_MODEL': 'openai/gpt-4o',
                'OPENHANDS_LLM_API_KEY': 'llm-key',
                'OPENHANDS_SKIP_TESTING': 'true',
                'OPENHANDS_TESTING_CONTAINER_ENABLED': 'true',
            }
        )

        self.assertEqual(errors, [])

    def test_validate_openhands_env_requires_testing_base_url_when_testing_container_enabled(self) -> None:
        errors = validate_openhands_env(
            {
                'OH_SECRET_KEY': 'secret-key',
                'OPENHANDS_LLM_MODEL': 'openai/gpt-4o',
                'OPENHANDS_LLM_API_KEY': 'llm-key',
                'OPENHANDS_TESTING_CONTAINER_ENABLED': 'true',
                'OPENHANDS_TESTING_LLM_MODEL': 'openai/gpt-4o-mini',
                'OPENHANDS_TESTING_LLM_API_KEY': 'testing-key',
            }
        )

        self.assertIn(
            'dedicated testing container requires OPENHANDS_TESTING_BASE_URL',
            errors,
        )

    def test_validate_openhands_env_requires_testing_model_when_testing_container_enabled(self) -> None:
        errors = validate_openhands_env(
            {
                'OH_SECRET_KEY': 'secret-key',
                'OPENHANDS_LLM_MODEL': 'openai/gpt-4o',
                'OPENHANDS_LLM_API_KEY': 'llm-key',
                'OPENHANDS_TESTING_CONTAINER_ENABLED': 'true',
                'OPENHANDS_TESTING_BASE_URL': 'http://localhost:3001',
            }
        )

        self.assertIn(
            'dedicated testing container requires OPENHANDS_TESTING_LLM_MODEL',
            errors,
        )

    def test_validate_openhands_env_requires_testing_api_key_for_non_bedrock_testing_model(self) -> None:
        errors = validate_openhands_env(
            {
                'OH_SECRET_KEY': 'secret-key',
                'OPENHANDS_LLM_MODEL': 'openai/gpt-4o',
                'OPENHANDS_LLM_API_KEY': 'llm-key',
                'OPENHANDS_TESTING_CONTAINER_ENABLED': 'true',
                'OPENHANDS_TESTING_BASE_URL': 'http://localhost:3001',
                'OPENHANDS_TESTING_LLM_MODEL': 'openai/gpt-4o-mini',
            }
        )

        self.assertIn(
            'openai/gpt-4o-mini requires OPENHANDS_TESTING_LLM_API_KEY',
            errors,
        )

    def test_validate_openhands_env_accepts_testing_container_with_bedrock_testing_model(self) -> None:
        errors = validate_openhands_env(
            {
                'OH_SECRET_KEY': 'secret-key',
                'OPENHANDS_LLM_MODEL': 'openai/gpt-4o',
                'OPENHANDS_LLM_API_KEY': 'llm-key',
                'OPENHANDS_TESTING_CONTAINER_ENABLED': 'true',
                'OPENHANDS_TESTING_BASE_URL': 'http://localhost:3001',
                'OPENHANDS_TESTING_LLM_MODEL': 'bedrock/anthropic.claude-3-sonnet-20240229-v1:0',
                'AWS_BEARER_TOKEN_BEDROCK': 'token',
            }
        )

        self.assertEqual(errors, [])

    def test_validate_openhands_env_rejects_incomplete_bedrock_testing_auth(self) -> None:
        errors = validate_openhands_env(
            {
                'OH_SECRET_KEY': 'secret-key',
                'OPENHANDS_LLM_MODEL': 'openai/gpt-4o',
                'OPENHANDS_LLM_API_KEY': 'llm-key',
                'OPENHANDS_TESTING_CONTAINER_ENABLED': 'true',
                'OPENHANDS_TESTING_BASE_URL': 'http://localhost:3001',
                'OPENHANDS_TESTING_LLM_MODEL': 'bedrock/anthropic.claude-3-sonnet-20240229-v1:0',
                'AWS_ACCESS_KEY_ID': 'key',
            }
        )

        self.assertIn(
            'bedrock model requires AWS_BEARER_TOKEN_BEDROCK or '
            'AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_REGION_NAME',
            errors,
        )

    def test_validate_openhands_env_requires_secret_key(self) -> None:
        errors = validate_openhands_env(
            {
                'OPENHANDS_LLM_MODEL': 'openai/gpt-4o',
                'OPENHANDS_LLM_API_KEY': 'llm-key',
            }
        )

        self.assertIn('missing required OpenHands env var: OH_SECRET_KEY', errors)

    def test_validate_openhands_env_accepts_bedrock_access_key_flow(self) -> None:
        errors = validate_openhands_env(
            {
                'OH_SECRET_KEY': 'secret-key',
                'OPENHANDS_LLM_MODEL': 'bedrock/anthropic.claude-3-sonnet-20240229-v1:0',
                'AWS_ACCESS_KEY_ID': 'key',
                'AWS_SECRET_ACCESS_KEY': 'secret',
                'AWS_REGION_NAME': 'us-west-2',
                'AWS_SESSION_TOKEN': 'session-token',
            }
        )

        self.assertEqual(errors, [])

    def test_validate_openhands_env_accepts_openrouter_model_with_base_url(self) -> None:
        errors = validate_openhands_env(
            {
                'OH_SECRET_KEY': 'secret-key',
                'OPENHANDS_LLM_MODEL': 'openrouter/openai/gpt-4o-mini',
                'OPENHANDS_LLM_API_KEY': 'router-key',
                'OPENHANDS_LLM_BASE_URL': 'https://openrouter.ai/api/v1',
            }
        )

        self.assertEqual(errors, [])

    def test_validate_openhands_env_accepts_bedrock_bearer_token(self) -> None:
        errors = validate_openhands_env(
            {
                'OH_SECRET_KEY': 'secret-key',
                'OPENHANDS_LLM_MODEL': 'bedrock/anthropic.claude-3-sonnet-20240229-v1:0',
                'AWS_BEARER_TOKEN_BEDROCK': 'token',
            }
        )

        self.assertEqual(errors, [])

    def test_validate_openhands_env_rejects_incomplete_bedrock_auth(self) -> None:
        errors = validate_openhands_env(
            {
                'OH_SECRET_KEY': 'secret-key',
                'OPENHANDS_LLM_MODEL': 'bedrock/anthropic.claude-3-sonnet-20240229-v1:0',
                'AWS_ACCESS_KEY_ID': 'key',
            }
        )

        self.assertEqual(
            errors,
            [
                'bedrock model requires AWS_BEARER_TOKEN_BEDROCK or '
                'AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_REGION_NAME'
            ],
        )

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

    @staticmethod
    def _validate_agent_env(env: dict[str, str]) -> list[str]:
        with patch('kato.validate_env.discover_git_repositories', return_value=[]):
            return validate_agent_env(env)
