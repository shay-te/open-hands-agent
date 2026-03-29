from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class DeploymentFilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        required_paths = [
            REPO_ROOT / 'docker-compose.yaml',
            REPO_ROOT / 'AGENTS.md',
            REPO_ROOT / 'README.md',
            REPO_ROOT / '.env.example',
            REPO_ROOT / 'Makefile',
        ]
        missing_paths = [str(path.name) for path in required_paths if not path.exists()]
        if missing_paths:
            raise unittest.SkipTest(
                f'deployment files not present; skipping ({", ".join(missing_paths)})'
            )

    def test_docker_compose_centralizes_openhands_llm_configuration(self) -> None:
        compose_text = (REPO_ROOT / 'docker-compose.yaml').read_text(encoding='utf-8')

        self.assertIn('LLM_MODEL: ${OPENHANDS_LLM_MODEL:-}', compose_text)
        self.assertIn('OPENHANDS_LLM_MODEL: ${OPENHANDS_LLM_MODEL:-}', compose_text)
        self.assertIn('LLM_API_KEY: ${OPENHANDS_LLM_API_KEY:-}', compose_text)
        self.assertIn('LLM_BASE_URL: ${OPENHANDS_LLM_BASE_URL:-}', compose_text)
        self.assertIn('OPENHANDS_LLM_BASE_URL: ${OPENHANDS_LLM_BASE_URL:-}', compose_text)
        self.assertIn(
            'OPENHANDS_POLL_INTERVAL_SECONDS: ${OPENHANDS_POLL_INTERVAL_SECONDS:-2.0}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_MAX_POLL_ATTEMPTS: ${OPENHANDS_MAX_POLL_ATTEMPTS:-900}',
            compose_text,
        )
        self.assertIn('OH_SECRET_KEY: ${OH_SECRET_KEY:-}', compose_text)
        self.assertIn('LOG_LEVEL: ${OPENHANDS_LOG_LEVEL:-error}', compose_text)
        self.assertIn('UVICORN_LOG_LEVEL: ${OPENHANDS_LOG_LEVEL:-error}', compose_text)
        self.assertIn('AWS_ACCESS_KEY_ID: ${AWS_ACCESS_KEY_ID:-}', compose_text)
        self.assertIn('AWS_SECRET_ACCESS_KEY: ${AWS_SECRET_ACCESS_KEY:-}', compose_text)
        self.assertIn('AWS_REGION_NAME: ${AWS_REGION_NAME:-}', compose_text)
        self.assertIn(
            'AWS_BEARER_TOKEN_BEDROCK: ${AWS_BEARER_TOKEN_BEDROCK:-}',
            compose_text,
        )
        self.assertIn(
            'SANDBOX_VOLUMES: ${REPOSITORY_ROOT_PATH:-.}:/workspace/project:rw,${OPENHANDS_SSH_AUTH_SOCK_HOST_PATH:-/run/host-services/ssh-auth.sock}:/ssh-agent:ro',
            compose_text,
        )
        self.assertIn('SSH_AUTH_SOCK: /ssh-agent', compose_text)
        self.assertIn('OH_WEB_URL: ${OPENHANDS_WEB_URL:-}', compose_text)
        self.assertIn('OH_PERSISTENCE_DIR: /.openhands', compose_text)
        self.assertIn(
            'OPENHANDS_AGENT_ISSUE_PLATFORM: ${OPENHANDS_AGENT_ISSUE_PLATFORM:-}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_AGENT_TICKET_SYSTEM: ${OPENHANDS_AGENT_TICKET_SYSTEM:-youtrack}',
            compose_text,
        )
        self.assertIn(
            'YOUTRACK_ISSUE_STATES: ${YOUTRACK_ISSUE_STATES:-Todo,Open}',
            compose_text,
        )
        self.assertIn(
            'YOUTRACK_PROGRESS_STATE: ${YOUTRACK_PROGRESS_STATE:-In Progress}',
            compose_text,
        )
        self.assertIn(
            'YOUTRACK_REVIEW_STATE: ${YOUTRACK_REVIEW_STATE:-To Verify}',
            compose_text,
        )
        self.assertIn(
            'JIRA_ISSUE_STATES: ${JIRA_ISSUE_STATES:-To Do,Open}',
            compose_text,
        )
        self.assertIn(
            'JIRA_PROGRESS_STATE: ${JIRA_PROGRESS_STATE:-In Progress}',
            compose_text,
        )
        self.assertIn(
            'GITHUB_ISSUES_ISSUE_STATES: ${GITHUB_ISSUES_ISSUE_STATES:-open}',
            compose_text,
        )
        self.assertIn(
            'GITHUB_ISSUES_PROGRESS_STATE: ${GITHUB_ISSUES_PROGRESS_STATE:-In Progress}',
            compose_text,
        )
        self.assertIn(
            'GITLAB_ISSUES_ISSUE_STATES: ${GITLAB_ISSUES_ISSUE_STATES:-opened}',
            compose_text,
        )
        self.assertIn(
            'GITLAB_ISSUES_PROGRESS_STATE: ${GITLAB_ISSUES_PROGRESS_STATE:-In Progress}',
            compose_text,
        )
        self.assertIn(
            'BITBUCKET_ISSUES_ISSUE_STATES: ${BITBUCKET_ISSUES_ISSUE_STATES:-new,open}',
            compose_text,
        )
        self.assertIn(
            'BITBUCKET_ISSUES_PROGRESS_STATE: ${BITBUCKET_ISSUES_PROGRESS_STATE:-open}',
            compose_text,
        )
        self.assertIn('GITHUB_API_TOKEN: ${GITHUB_API_TOKEN:-}', compose_text)
        self.assertIn('GITLAB_API_TOKEN: ${GITLAB_API_TOKEN:-}', compose_text)
        self.assertIn('BITBUCKET_API_TOKEN: ${BITBUCKET_API_TOKEN:-}', compose_text)
        self.assertIn(
            'OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED: ${OPENHANDS_AGENT_COMPLETION_EMAIL_ENABLED:-false}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_AGENT_LOG_LEVEL: ${OPENHANDS_AGENT_LOG_LEVEL:-warning}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_AGENT_WORKFLOW_LOG_LEVEL: ${OPENHANDS_AGENT_WORKFLOW_LOG_LEVEL:-info}',
            compose_text,
        )
        self.assertIn('REPOSITORY_ROOT_PATH: ${REPOSITORY_ROOT_PATH:-.}', compose_text)
        self.assertIn(
            'OPENHANDS_AGENT_STATE_FILE: ${OPENHANDS_AGENT_STATE_FILE:-data/openhands_agent_state.json}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_AGENT_DB_PATH: ${OPENHANDS_AGENT_DB_PATH:-data}',
            compose_text,
        )
        self.assertIn('OH_AGENT_SERVER_ENV: >-', compose_text)
        self.assertIn(
            '"SSH_AUTH_SOCK":"/ssh-agent"',
            compose_text,
        )
        self.assertIn(
            '"OH_SANDBOX_VOLUMES":"${REPOSITORY_ROOT_PATH:-.}:/workspace/project:rw,${OPENHANDS_SSH_AUTH_SOCK_HOST_PATH:-/run/host-services/ssh-auth.sock}:/ssh-agent:ro"',
            compose_text,
        )
        self.assertIn(
            '"LLM_AWS_ACCESS_KEY_ID":"${AWS_ACCESS_KEY_ID:-}"',
            compose_text,
        )
        self.assertIn('OH_PERSISTENCE_DIR: /data', compose_text)

    def test_env_example_includes_openhands_llm_variables(self) -> None:
        env_example_text = (REPO_ROOT / '.env.example').read_text(encoding='utf-8')

        self.assertIn('OPENHANDS_AGENT_ISSUE_PLATFORM=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_TICKET_SYSTEM=', env_example_text)
        self.assertIn('REPOSITORY_ROOT_PATH=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_IGNORED_REPOSITORY_FOLDERS=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_DB_PROTOCOL=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_DB_PATH=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_DB_FILE=', env_example_text)
        self.assertIn('JIRA_BASE_URL=', env_example_text)
        self.assertIn('JIRA_TOKEN=', env_example_text)
        self.assertIn('YOUTRACK_PROGRESS_STATE=', env_example_text)
        self.assertIn('YOUTRACK_ISSUE_STATES=', env_example_text)
        self.assertIn('JIRA_PROGRESS_STATE=', env_example_text)
        self.assertIn('JIRA_ISSUE_STATES=', env_example_text)
        self.assertIn('GITHUB_ISSUES_BASE_URL=', env_example_text)
        self.assertIn('GITHUB_API_TOKEN=', env_example_text)
        self.assertIn('GITHUB_ISSUES_PROGRESS_STATE=', env_example_text)
        self.assertIn('GITHUB_ISSUES_ISSUE_STATES=', env_example_text)
        self.assertIn('GITLAB_ISSUES_BASE_URL=', env_example_text)
        self.assertIn('GITLAB_API_TOKEN=', env_example_text)
        self.assertIn('GITLAB_ISSUES_PROGRESS_STATE=', env_example_text)
        self.assertIn('GITLAB_ISSUES_ISSUE_STATES=', env_example_text)
        self.assertIn('BITBUCKET_ISSUES_BASE_URL=', env_example_text)
        self.assertIn('BITBUCKET_API_TOKEN=', env_example_text)
        self.assertIn('BITBUCKET_ISSUES_PROGRESS_STATE=', env_example_text)
        self.assertIn('BITBUCKET_ISSUES_ISSUE_STATES=', env_example_text)
        self.assertIn('OPENHANDS_BASE_URL=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_STATE_FILE=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_LOG_LEVEL=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_WORKFLOW_LOG_LEVEL=', env_example_text)
        self.assertIn('OPENHANDS_SSH_AUTH_SOCK_HOST_PATH=', env_example_text)
        self.assertIn('OPENHANDS_LLM_MODEL=', env_example_text)
        self.assertIn('OPENHANDS_LLM_API_KEY=', env_example_text)
        self.assertIn('OPENHANDS_LLM_BASE_URL=', env_example_text)
        self.assertIn('OPENHANDS_POLL_INTERVAL_SECONDS=', env_example_text)
        self.assertIn('OPENHANDS_MAX_POLL_ATTEMPTS=', env_example_text)
        self.assertIn('OPENHANDS_LOG_LEVEL=', env_example_text)
        self.assertIn('OH_SECRET_KEY=', env_example_text)
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
        self.assertIn('make configure', readme_text)

    def test_repo_includes_bootstrap_automation_files(self) -> None:
        bootstrap_text = (REPO_ROOT / 'scripts' / 'bootstrap.sh').read_text(encoding='utf-8')
        run_local_text = (REPO_ROOT / 'scripts' / 'run-local.sh').read_text(encoding='utf-8')
        makefile_text = (REPO_ROOT / 'Makefile').read_text(encoding='utf-8')
        compose_text = (REPO_ROOT / 'docker-compose.yaml').read_text(encoding='utf-8')
        install_entrypoint_text = (
            REPO_ROOT / 'docker' / 'entrypoint-install.sh'
        ).read_text(encoding='utf-8')
        config_text = (
            REPO_ROOT / 'openhands_agent' / 'config' / 'openhands_agent_core_lib.yaml'
        ).read_text(encoding='utf-8')
        install_deps_text = (
            REPO_ROOT / 'scripts' / 'install-python-deps.sh'
        ).read_text(encoding='utf-8')

        self.assertIn('cp .env.example .env', bootstrap_text)
        self.assertIn('sh ./scripts/install-python-deps.sh .venv/bin/python editable', bootstrap_text)
        self.assertIn('.venv/bin/python -m unittest discover -s tests', bootstrap_text)
        self.assertIn('hydra-core>=1.3.2', install_deps_text)
        self.assertIn('core-lib>=0.2.0', install_deps_text)
        self.assertNotIn('deps-only', install_deps_text)
        self.assertNotIn('openhands_agent.validate_env --mode agent', run_local_text)
        self.assertIn('openhands_agent.install', run_local_text)
        self.assertIn('bootstrap:', makefile_text)
        self.assertIn('configure:', makefile_text)
        self.assertIn('scripts/generate_env.py --output .env', makefile_text)
        self.assertIn('doctor:', makefile_text)
        self.assertIn('install:', makefile_text)
        self.assertIn('run:', makefile_text)
        self.assertNotIn('.docker-compose.selected-repos.yaml', makefile_text)
        self.assertIn('python -m openhands_agent.install', install_entrypoint_text)
        self.assertIn('install:', compose_text)
        self.assertIn('/app/docker/entrypoint-install.sh', compose_text)
        self.assertIn('/app/docker/entrypoint-run.sh', compose_text)
        self.assertIn('openhands-agent-data:/app/data', compose_text)
        self.assertIn('${REPOSITORY_ROOT_PATH:-.}:/workspace/project', compose_text)
        self.assertIn('REPOSITORY_ROOT_PATH: /workspace/project', compose_text)
        self.assertIn('${REPOSITORY_ROOT_PATH:-.}:/workspace/project:ro', compose_text)
        self.assertIn('docker.openhands.dev/openhands/openhands:1.5', compose_text)
        self.assertNotIn('/Users/shaytessler/Desktop/dev/openhands-agent:/workspace/project', compose_text)
        dockerfile_text = (REPO_ROOT / 'Dockerfile').read_text(encoding='utf-8')
        self.assertIn('COPY . .', dockerfile_text)
        self.assertNotIn('COPY pyproject.toml ./', dockerfile_text)
        self.assertIn('apt-get install -y --no-install-recommends git', dockerfile_text)
        self.assertIn(
            'chmod +x /app/docker/entrypoint-run.sh /app/docker/entrypoint-install.sh',
            dockerfile_text,
        )
        self.assertNotIn('CMD ["/app/docker/entrypoint-run.sh"]', dockerfile_text)
        self.assertNotIn(
            'RUN sh /app/scripts/install-python-deps.sh python deps-only',
            dockerfile_text,
        )
        self.assertIn(
            'AGENT_SERVER_IMAGE_REPOSITORY: ${OPENHANDS_AGENT_SERVER_IMAGE_REPOSITORY:-ghcr.io/openhands/agent-server}',
            compose_text,
        )
        self.assertIn(
            'AGENT_SERVER_IMAGE_TAG: ${OPENHANDS_AGENT_SERVER_IMAGE_TAG:-1.12.0-python}',
            compose_text,
        )
        openhands_section = compose_text.split('  install:')[0]
        self.assertIn(
            'AGENT_SERVER_IMAGE_REPOSITORY: ${OPENHANDS_AGENT_SERVER_IMAGE_REPOSITORY:-ghcr.io/openhands/agent-server}',
            openhands_section,
        )
        self.assertIn(
            'AGENT_SERVER_IMAGE_TAG: ${OPENHANDS_AGENT_SERVER_IMAGE_TAG:-1.12.0-python}',
            openhands_section,
        )
        self.assertIn('create_db: true', config_text)
        self.assertIn('repositories:', config_text)
        self.assertIn('YOUTRACK_ISSUE_STATES', config_text)
        self.assertIn('poll_interval_seconds:', config_text)
        self.assertIn('max_poll_attempts:', config_text)

    def test_repo_includes_ci_workflow(self) -> None:
        workflow_text = (
            REPO_ROOT / '.github' / 'workflows' / 'ci.yml'
        ).read_text(encoding='utf-8')

        self.assertIn('python -m unittest discover -s tests', workflow_text)
        self.assertIn(
            'shellcheck scripts/bootstrap.sh scripts/install-python-deps.sh scripts/run-local.sh docker/entrypoint-run.sh docker/entrypoint-install.sh',
            workflow_text,
        )
