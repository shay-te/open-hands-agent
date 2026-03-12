import unittest

from openhands_agent.validate_env import (
    validate_agent_env,
    validate_openhands_env,
)


class ValidateEnvTests(unittest.TestCase):
    def test_validate_agent_env_accepts_complete_configuration(self) -> None:
        errors = validate_agent_env(
            {
                'YOUTRACK_BASE_URL': 'https://youtrack.example',
                'YOUTRACK_TOKEN': 'yt-token',
                'YOUTRACK_PROJECT': 'PROJ',
                'BITBUCKET_BASE_URL': 'https://bitbucket.example',
                'BITBUCKET_TOKEN': 'bb-token',
                'BITBUCKET_WORKSPACE': 'workspace',
                'BITBUCKET_REPO_SLUG': 'repo',
                'OPENHANDS_BASE_URL': 'http://localhost:3000',
                'OPENHANDS_API_KEY': 'local',
            }
        )

        self.assertEqual(errors, [])

    def test_validate_agent_env_requires_email_fields_when_enabled(self) -> None:
        errors = validate_agent_env(
            {
                'YOUTRACK_BASE_URL': 'https://youtrack.example',
                'YOUTRACK_TOKEN': 'yt-token',
                'YOUTRACK_PROJECT': 'PROJ',
                'BITBUCKET_BASE_URL': 'https://bitbucket.example',
                'BITBUCKET_TOKEN': 'bb-token',
                'BITBUCKET_WORKSPACE': 'workspace',
                'BITBUCKET_REPO_SLUG': 'repo',
                'OPENHANDS_BASE_URL': 'http://localhost:3000',
                'OPENHANDS_API_KEY': 'local',
                'OPENHANDS_AGENT_FAILURE_EMAIL_ENABLED': 'true',
            }
        )

        self.assertIn(
            'failure email is enabled but OPENHANDS_AGENT_FAILURE_EMAIL_TEMPLATE_ID is missing',
            errors,
        )
        self.assertIn(
            'failure email is enabled but OPENHANDS_AGENT_FAILURE_EMAIL_TO is missing',
            errors,
        )

    def test_validate_openhands_env_requires_api_key_for_non_bedrock_models(self) -> None:
        errors = validate_openhands_env(
            {
                'OPENHANDS_LLM_MODEL': 'openai/gpt-4o',
            }
        )

        self.assertEqual(errors, ['openai/gpt-4o requires OPENHANDS_LLM_API_KEY'])

    def test_validate_openhands_env_accepts_bedrock_access_key_flow(self) -> None:
        errors = validate_openhands_env(
            {
                'OPENHANDS_LLM_MODEL': 'bedrock/anthropic.claude-3-sonnet-20240229-v1:0',
                'AWS_ACCESS_KEY_ID': 'key',
                'AWS_SECRET_ACCESS_KEY': 'secret',
                'AWS_REGION_NAME': 'us-west-2',
            }
        )

        self.assertEqual(errors, [])

    def test_validate_openhands_env_accepts_bedrock_bearer_token(self) -> None:
        errors = validate_openhands_env(
            {
                'OPENHANDS_LLM_MODEL': 'bedrock/anthropic.claude-3-sonnet-20240229-v1:0',
                'AWS_BEARER_TOKEN_BEDROCK': 'token',
            }
        )

        self.assertEqual(errors, [])

    def test_validate_openhands_env_rejects_incomplete_bedrock_auth(self) -> None:
        errors = validate_openhands_env(
            {
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
