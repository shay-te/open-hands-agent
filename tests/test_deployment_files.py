from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class DeploymentFilesTests(unittest.TestCase):
    def test_docker_compose_centralizes_openhands_llm_configuration(self) -> None:
        compose_text = (REPO_ROOT / 'docker-compose.yml').read_text(encoding='utf-8')

        self.assertIn('LLM_MODEL: ${OPENHANDS_LLM_MODEL:-}', compose_text)
        self.assertIn('LLM_API_KEY: ${OPENHANDS_LLM_API_KEY:-}', compose_text)
        self.assertIn('LLM_BASE_URL: ${OPENHANDS_LLM_BASE_URL:-}', compose_text)
        self.assertIn(
            'AWS_BEARER_TOKEN_BEDROCK: ${AWS_BEARER_TOKEN_BEDROCK:-}',
            compose_text,
        )
        self.assertIn('OH_WEB_URL: ${OPENHANDS_WEB_URL:-}', compose_text)
        self.assertIn(
            'OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED: ${OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED:-false}',
            compose_text,
        )

    def test_env_example_includes_openhands_llm_variables(self) -> None:
        env_example_text = (REPO_ROOT / '.env.example').read_text(encoding='utf-8')

        self.assertIn('OPENHANDS_LLM_MODEL=', env_example_text)
        self.assertIn('OPENHANDS_LLM_API_KEY=', env_example_text)
        self.assertIn('OPENHANDS_LLM_BASE_URL=', env_example_text)
        self.assertIn('EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY=', env_example_text)
        self.assertNotIn('EMIL_CORE_LIB_SEND_IN_BLUE_API_KEY=', env_example_text)
        self.assertIn('AWS_ACCESS_KEY_ID=', env_example_text)
        self.assertIn('AWS_BEARER_TOKEN_BEDROCK=', env_example_text)

    def test_agents_file_exists_for_openhands_rules(self) -> None:
        agents_text = (REPO_ROOT / 'AGENTS.md').read_text(encoding='utf-8')

        self.assertIn('Keep orchestration logic in services.', agents_text)
        self.assertIn('Prefer constants from `fields.py` over free-text field names.', agents_text)
        self.assertIn('Write tests for new behavior when possible.', agents_text)
        self.assertIn('Run the relevant tests before opening a pull request.', agents_text)
        self.assertIn(
            'Add edge-case coverage for malformed payloads, retries, timeouts, and degraded downstream behavior when relevant.',
            agents_text,
        )
