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
            'OPENHANDS_AGENT_ISSUE_PLATFORM: ${OPENHANDS_AGENT_ISSUE_PLATFORM:-}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_AGENT_TICKET_SYSTEM: ${OPENHANDS_AGENT_TICKET_SYSTEM:-youtrack}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED: ${OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED:-false}',
            compose_text,
        )
        self.assertIn('REPOSITORY_LOCAL_PATH: ${REPOSITORY_LOCAL_PATH:-.}', compose_text)

    def test_env_example_includes_openhands_llm_variables(self) -> None:
        env_example_text = (REPO_ROOT / '.env.example').read_text(encoding='utf-8')

        self.assertIn('REPOSITORY_ID=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_ISSUE_PLATFORM=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_TICKET_SYSTEM=', env_example_text)
        self.assertIn('REPOSITORY_LOCAL_PATH=', env_example_text)
        self.assertIn('REPOSITORY_BASE_URL=', env_example_text)
        self.assertIn('REPOSITORY_TOKEN=', env_example_text)
        self.assertIn('REPOSITORY_OWNER=', env_example_text)
        self.assertIn('REPOSITORY_REPO_SLUG=', env_example_text)
        self.assertIn('JIRA_BASE_URL=', env_example_text)
        self.assertIn('JIRA_TOKEN=', env_example_text)
        self.assertIn('GITHUB_ISSUES_BASE_URL=', env_example_text)
        self.assertIn('GITLAB_ISSUES_BASE_URL=', env_example_text)
        self.assertIn('BITBUCKET_ISSUES_BASE_URL=', env_example_text)
        self.assertIn('OPENHANDS_BASE_URL=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_STATE_FILE=', env_example_text)
        self.assertIn('OPENHANDS_LLM_MODEL=', env_example_text)
        self.assertIn('OPENHANDS_LLM_API_KEY=', env_example_text)
        self.assertIn('OPENHANDS_LLM_BASE_URL=', env_example_text)
        self.assertIn('EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY=', env_example_text)
        self.assertNotIn('EMIL_CORE_LIB_SEND_IN_BLUE_API_KEY=', env_example_text)
        self.assertIn('AWS_ACCESS_KEY_ID=', env_example_text)
        self.assertIn('AWS_BEARER_TOKEN_BEDROCK=', env_example_text)

    def test_agents_file_exists_for_openhands_rules(self) -> None:
        agents_text = (REPO_ROOT / 'AGENTS.md').read_text(encoding='utf-8')
        readme_text = (REPO_ROOT / 'README.md').read_text(encoding='utf-8')

        self.assertIn('Keep orchestration logic in services.', agents_text)
        self.assertIn('Prefer constants from `fields.py` over free-text field names.', agents_text)
        self.assertIn('Write tests for new behavior when possible.', agents_text)
        self.assertIn('Run the relevant tests before opening a pull request.', agents_text)
        self.assertIn(
            'Add edge-case coverage for malformed payloads, retries, timeouts, and degraded downstream behavior when relevant.',
            agents_text,
        )
        self.assertNotIn('/Users/shaytessler/', readme_text)

    def test_repo_includes_bootstrap_automation_files(self) -> None:
        bootstrap_text = (REPO_ROOT / 'scripts' / 'bootstrap.sh').read_text(encoding='utf-8')
        run_local_text = (REPO_ROOT / 'scripts' / 'run-local.sh').read_text(encoding='utf-8')
        makefile_text = (REPO_ROOT / 'Makefile').read_text(encoding='utf-8')
        config_text = (
            REPO_ROOT / 'openhands_agent' / 'config' / 'openhands_agent_core_lib.yaml'
        ).read_text(encoding='utf-8')

        self.assertIn('cp .env.example .env', bootstrap_text)
        self.assertIn('.venv/bin/python -m pip install -e .', bootstrap_text)
        self.assertIn('.venv/bin/python -m unittest discover -s tests', bootstrap_text)
        self.assertNotIn('openhands_agent.validate_env --mode agent', run_local_text)
        self.assertIn('openhands_agent.create_db', run_local_text)
        self.assertIn('bootstrap:', makefile_text)
        self.assertIn('doctor:', makefile_text)
        self.assertIn('run:', makefile_text)
        self.assertIn('create_db: true', config_text)
        self.assertIn('repositories:', config_text)

    def test_repo_includes_ci_workflow(self) -> None:
        workflow_text = (
            REPO_ROOT / '.github' / 'workflows' / 'ci.yml'
        ).read_text(encoding='utf-8')

        self.assertIn('python -m unittest discover -s tests', workflow_text)
        self.assertIn('shellcheck scripts/bootstrap.sh scripts/run-local.sh docker/entrypoint-run.sh', workflow_text)
