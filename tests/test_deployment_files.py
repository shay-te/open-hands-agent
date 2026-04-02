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
            'OPENHANDS_MODEL_SMOKE_TEST_ENABLED: ${OPENHANDS_MODEL_SMOKE_TEST_ENABLED:-true}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_TESTING_CONTAINER_ENABLED: ${OPENHANDS_TESTING_CONTAINER_ENABLED:-false}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_SKIP_TESTING: ${OPENHANDS_SKIP_TESTING:-false}',
            compose_text,
        )
        self.assertIn(
            'LOG_ALL_EVENTS: ${OPENHANDS_CONTAINER_LOG_ALL_EVENTS}',
            compose_text,
        )
        self.assertIn('AWS_ACCESS_KEY_ID: ${AWS_ACCESS_KEY_ID:-}', compose_text)
        self.assertIn('AWS_SECRET_ACCESS_KEY: ${AWS_SECRET_ACCESS_KEY:-}', compose_text)
        self.assertIn('AWS_REGION_NAME: ${AWS_REGION_NAME:-}', compose_text)
        self.assertIn('LLM_AWS_ACCESS_KEY_ID: ${AWS_ACCESS_KEY_ID:-}', compose_text)
        self.assertIn('LLM_AWS_SECRET_ACCESS_KEY: ${AWS_SECRET_ACCESS_KEY:-}', compose_text)
        self.assertIn('LLM_AWS_REGION_NAME: ${AWS_REGION_NAME:-}', compose_text)
        self.assertIn('AWS_DEFAULT_REGION: ${AWS_REGION_NAME:-}', compose_text)
        self.assertIn('OPENHANDS_TESTING_BASE_URL: http://openhands-testing:3000', compose_text)
        self.assertIn(
            'OPENHANDS_TESTING_LLM_MODEL: ${OPENHANDS_TESTING_LLM_MODEL:-}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_TESTING_LLM_API_KEY: ${OPENHANDS_TESTING_LLM_API_KEY:-}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_TESTING_LLM_BASE_URL: ${OPENHANDS_TESTING_LLM_BASE_URL:-}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_POLL_INTERVAL_SECONDS: ${OPENHANDS_POLL_INTERVAL_SECONDS:-2.0}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_MAX_POLL_ATTEMPTS: ${OPENHANDS_MAX_POLL_ATTEMPTS:-900}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_TASK_SCAN_STARTUP_DELAY_SECONDS: ${OPENHANDS_TASK_SCAN_STARTUP_DELAY_SECONDS:-30}',
            compose_text,
        )
        self.assertIn(
            'OPENHANDS_TASK_SCAN_INTERVAL_SECONDS: ${OPENHANDS_TASK_SCAN_INTERVAL_SECONDS:-60}',
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
        self.assertIn(
            'MOUNT_DOCKER_DATA_ROOT=./mount_docker_data',
            (REPO_ROOT / '.env.example').read_text(encoding='utf-8'),
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
        self.assertIn('BITBUCKET_USERNAME: ${BITBUCKET_USERNAME:-}', compose_text)
        self.assertIn('BITBUCKET_API_EMAIL: ${BITBUCKET_API_EMAIL:-}', compose_text)
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
        self.assertIn('openhands-testing:', compose_text)
        self.assertIn('profiles: ["testing"]', compose_text)
        self.assertIn(
            '"${OPENHANDS_TESTING_PORT:-3001}:3000"',
            compose_text,
        )
        self.assertIn(
            'OH_AGENT_SERVER_ENV: \'{"LLM_API_KEY":"${AWS_ACCESS_KEY_ID:-}/${AWS_SECRET_ACCESS_KEY:-}/${AWS_REGION_NAME:-}", "AWS_ACCESS_KEY_ID":"${AWS_ACCESS_KEY_ID:-}", "AWS_SECRET_ACCESS_KEY":"${AWS_SECRET_ACCESS_KEY:-}", "AWS_DEFAULT_REGION":"${AWS_REGION_NAME:-}", "AWS_REGION":"${AWS_REGION_NAME:-}", "LLM_AWS_ACCESS_KEY_ID":"${AWS_ACCESS_KEY_ID:-}", "LLM_AWS_SECRET_ACCESS_KEY":"${AWS_SECRET_ACCESS_KEY:-}", "LLM_AWS_REGION_NAME":"${AWS_REGION_NAME:-}", "OH_SANDBOX_VOLUMES":"${REPOSITORY_ROOT_PATH:-.}:/workspace/project:rw"}\'',
            compose_text,
        )
        self.assertIn('OH_PERSISTENCE_DIR: /data', compose_text)
        self.assertIn(
            '- ${REPOSITORY_ROOT_PATH:-.}:/workspace/project:rw',
            compose_text,
        )
        self.assertNotIn(
            '- ${REPOSITORY_ROOT_PATH:-.}:/workspace/project:ro',
            compose_text,
        )
        self.assertNotIn('OPENHANDS_CONTAINER_LLM_MODEL', compose_text)
        self.assertNotIn('OPENHANDS_CONTAINER_AWS_ACCESS_KEY_ID', compose_text)
        self.assertNotIn('OPENHANDS_CONTAINER_AWS_SECRET_ACCESS_KEY', compose_text)
        self.assertNotIn('OPENHANDS_CONTAINER_AWS_REGION_NAME', compose_text)
        self.assertNotIn('OPENHANDS_CONTAINER_AWS_SESSION_TOKEN', compose_text)
        self.assertNotIn('OPENHANDS_CONTAINER_AWS_DEFAULT_REGION', compose_text)

    def test_env_example_includes_openhands_llm_variables(self) -> None:
        env_example_text = (REPO_ROOT / '.env.example').read_text(encoding='utf-8')

        self.assertIn('OPENHANDS_AGENT_ISSUE_PLATFORM=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_TICKET_SYSTEM=', env_example_text)
        self.assertIn('REPOSITORY_ROOT_PATH=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_IGNORED_REPOSITORY_FOLDERS=', env_example_text)
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
        self.assertIn('BITBUCKET_USERNAME=', env_example_text)
        self.assertIn('BITBUCKET_API_EMAIL=', env_example_text)
        self.assertIn('BITBUCKET_ISSUES_PROGRESS_STATE=', env_example_text)
        self.assertIn('BITBUCKET_ISSUES_ISSUE_STATES=', env_example_text)
        self.assertIn('OPENHANDS_BASE_URL=', env_example_text)
        self.assertIn('OPENHANDS_SKIP_TESTING=', env_example_text)
        self.assertIn('OPENHANDS_TESTING_CONTAINER_ENABLED=', env_example_text)
        self.assertIn('OPENHANDS_TESTING_BASE_URL=', env_example_text)
        self.assertIn('OPENHANDS_TESTING_PORT=', env_example_text)
        self.assertIn('OPENHANDS_CONTAINER_LOG_ALL_EVENTS=', env_example_text)
        self.assertNotIn('OPENHANDS_CONTAINER_LLM_MODEL=', env_example_text)
        self.assertNotIn('OPENHANDS_CONTAINER_AWS_ACCESS_KEY_ID=', env_example_text)
        self.assertNotIn('OPENHANDS_CONTAINER_AWS_SECRET_ACCESS_KEY=', env_example_text)
        self.assertNotIn('OPENHANDS_CONTAINER_AWS_REGION_NAME=', env_example_text)
        self.assertNotIn('OPENHANDS_CONTAINER_AWS_SESSION_TOKEN=', env_example_text)
        self.assertNotIn('OPENHANDS_CONTAINER_AWS_DEFAULT_REGION=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_LOG_LEVEL=', env_example_text)
        self.assertIn('OPENHANDS_AGENT_WORKFLOW_LOG_LEVEL=', env_example_text)
        self.assertIn('OPENHANDS_SSH_AUTH_SOCK_HOST_PATH=', env_example_text)
        self.assertIn('OPENHANDS_LLM_MODEL=', env_example_text)
        self.assertIn('OPENHANDS_LLM_API_KEY=', env_example_text)
        self.assertIn('OPENHANDS_LLM_BASE_URL=', env_example_text)
        self.assertIn('OPENHANDS_MODEL_SMOKE_TEST_ENABLED=', env_example_text)
        self.assertIn('OPENHANDS_TESTING_LLM_MODEL=', env_example_text)
        self.assertIn('OPENHANDS_TESTING_LLM_API_KEY=', env_example_text)
        self.assertIn('OPENHANDS_TESTING_LLM_BASE_URL=', env_example_text)
        self.assertIn('OPENHANDS_POLL_INTERVAL_SECONDS=', env_example_text)
        self.assertIn('OPENHANDS_MAX_POLL_ATTEMPTS=', env_example_text)
        self.assertIn('OPENHANDS_TASK_SCAN_STARTUP_DELAY_SECONDS=', env_example_text)
        self.assertIn('OPENHANDS_TASK_SCAN_INTERVAL_SECONDS=', env_example_text)
        self.assertIn('OPENHANDS_LOG_LEVEL=', env_example_text)
        self.assertIn('OH_SECRET_KEY=', env_example_text)
        self.assertIn('EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY=', env_example_text)
        self.assertNotIn('EMIL_CORE_LIB_SEND_IN_BLUE_API_KEY=', env_example_text)
        self.assertIn('AWS_ACCESS_KEY_ID=', env_example_text)
        self.assertIn('AWS_SESSION_TOKEN=', env_example_text)
        self.assertIn('AWS_BEARER_TOKEN_BEDROCK=', env_example_text)

    def test_agents_file_exists_for_openhands_rules(self) -> None:
        agents_text = (REPO_ROOT / 'AGENTS.md').read_text(encoding='utf-8')
        readme_text = (REPO_ROOT / 'README.md').read_text(encoding='utf-8')

        self.assertIn('Keep orchestration logic in services.', agents_text)
        self.assertIn(
            'Prefer constants from `openhands_agent/data_layers/data/fields.py` over free-text field names.',
            agents_text,
        )
        self.assertIn('Write tests for new behavior when possible.', agents_text)
        self.assertIn('Run the relevant tests before opening a pull request.', agents_text)
        self.assertIn(
            'Add edge-case coverage for malformed payloads, retries, timeouts, and degraded downstream behavior when relevant.',
            agents_text,
        )
        self.assertNotIn('/Users/shaytessler/', readme_text)
        self.assertIn('make configure', readme_text)
        self.assertFalse((REPO_ROOT / 'openhands_agent' / 'fields.py').exists())
        self.assertFalse((REPO_ROOT / 'openhands_agent' / 'error_handling.py').exists())
        self.assertFalse(
            any((REPO_ROOT / 'openhands_agent' / 'data_layers' / 'service' / 'validation').glob('*.py'))
        )

    def test_helper_modules_use_utils_suffix(self) -> None:
        helpers_dir = REPO_ROOT / 'openhands_agent' / 'helpers'

        helper_modules = [
            path
            for path in helpers_dir.glob('*.py')
            if path.name != '__init__.py'
        ]
        non_utils_modules = [
            path.name for path in helper_modules if not path.name.endswith('_utils.py')
        ]
        self.assertEqual(non_utils_modules, [])

    def test_validation_modules_live_in_top_level_validation_package(self) -> None:
        validation_dir = REPO_ROOT / 'openhands_agent' / 'validation'

        validation_modules = [
            path.name
            for path in validation_dir.glob('*.py')
            if path.name != '__init__.py'
        ]
        self.assertIn('base.py', validation_modules)
        self.assertIn('branch_publishability.py', validation_modules)
        self.assertIn('branch_push.py', validation_modules)
        self.assertIn('model_access.py', validation_modules)
        self.assertIn('repository_connections.py', validation_modules)
        self.assertIn('startup_dependency_validator.py', validation_modules)

    def test_repo_includes_bootstrap_automation_files(self) -> None:
        bootstrap_text = (REPO_ROOT / 'scripts' / 'bootstrap.sh').read_text(encoding='utf-8')
        run_local_text = (REPO_ROOT / 'scripts' / 'run-local.sh').read_text(encoding='utf-8')
        makefile_text = (REPO_ROOT / 'Makefile').read_text(encoding='utf-8')
        compose_text = (REPO_ROOT / 'docker-compose.yaml').read_text(encoding='utf-8')
        gitignore_text = (REPO_ROOT / '.gitignore').read_text(encoding='utf-8')
        install_entrypoint_text = (
            REPO_ROOT / 'docker' / 'entrypoint-install.sh'
        ).read_text(encoding='utf-8')
        run_entrypoint_text = (
            REPO_ROOT / 'docker' / 'entrypoint-run.sh'
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
        self.assertNotIn('alembic', install_deps_text)
        self.assertNotIn('sqlalchemy', install_deps_text)
        self.assertNotIn('pydantic', install_deps_text)
        self.assertNotIn('deps-only', install_deps_text)
        self.assertNotIn('openhands_agent.validate_env --mode agent', run_local_text)
        self.assertIn('openhands_agent.install', run_local_text)
        self.assertIn('bootstrap:', makefile_text)
        self.assertIn('configure:', makefile_text)
        self.assertIn('scripts/generate_env.py --output .env', makefile_text)
        self.assertIn('doctor:', makefile_text)
        self.assertIn('install:', makefile_text)
        self.assertIn('run:', makefile_text)
        self.assertIn('--profile testing', makefile_text)
        self.assertIn('OPENHANDS_SKIP_TESTING', makefile_text)
        self.assertIn('--attach install --attach openhands-agent', makefile_text)
        self.assertNotIn('.docker-compose.selected-repos.yaml', makefile_text)
        self.assertIn('python -m openhands_agent.install', install_entrypoint_text)
        self.assertIn('OPENHANDS_TESTING_CONTAINER_ENABLED', run_entrypoint_text)
        self.assertIn('OPENHANDS_SKIP_TESTING', run_entrypoint_text)
        self.assertIn('OPENHANDS_TESTING_BASE_URL', run_entrypoint_text)
        self.assertIn('BITBUCKET_USERNAME', run_entrypoint_text)
        self.assertIn('username=%s', run_entrypoint_text)
        self.assertNotIn('username=shacoshe', run_entrypoint_text)
        self.assertNotIn('BITBUCKET_API_USERNAME', compose_text)
        self.assertIn('install:', compose_text)
        self.assertIn('/app/docker/entrypoint-install.sh', compose_text)
        self.assertIn('/app/docker/entrypoint-run.sh', compose_text)
        self.assertIn('build/', gitignore_text)
        self.assertIn('dist/', gitignore_text)
        self.assertIn('out/', gitignore_text)
        self.assertIn('coverage/', gitignore_text)
        self.assertIn('target/', gitignore_text)
        self.assertIn(
            '${MOUNT_DOCKER_DATA_ROOT:-./mount_docker_data}/openhands:/data',
            compose_text,
        )
        self.assertIn(
            '${MOUNT_DOCKER_DATA_ROOT:-./mount_docker_data}/openhands_state:/.openhands',
            compose_text,
        )
        self.assertIn('${REPOSITORY_ROOT_PATH:-.}:/workspace/project', compose_text)
        self.assertIn('REPOSITORY_ROOT_PATH: /workspace/project', compose_text)
        self.assertIn('${REPOSITORY_ROOT_PATH:-.}:/workspace/project:rw', compose_text)
        self.assertIn('docker.openhands.dev/openhands/openhands:1.5', compose_text)
        self.assertNotIn('/Users/shaytessler/Desktop/dev/openhands-agent:/workspace/project', compose_text)
        self.assertNotIn('./docker_data/openhands:/data', compose_text)
        self.assertNotIn('./docker_data/openhands_state:/.openhands', compose_text)
        self.assertNotIn('docker_data/openhands-testing', compose_text)
        self.assertNotIn('docker_data/openhands_testing_state', compose_text)
        self.assertNotIn('openhands-agent-data:/app/data', compose_text)
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
        self.assertIn('repositories:', config_text)
        self.assertIn('YOUTRACK_ISSUE_STATES', config_text)
        self.assertIn('poll_interval_seconds:', config_text)
        self.assertIn('max_poll_attempts:', config_text)
        self.assertIn('skip_testing:', config_text)
        self.assertIn('testing_container_enabled:', config_text)
        self.assertIn('testing_base_url:', config_text)
        self.assertNotIn('alembic', (REPO_ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        self.assertNotIn('sqlalchemy', (REPO_ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        self.assertNotIn('pydantic', (REPO_ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        self.assertNotIn('script.py.mako', (REPO_ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        self.assertNotIn('database schema', (REPO_ROOT / 'README.md').read_text(encoding='utf-8'))

    def test_repo_includes_ci_workflow(self) -> None:
        workflow_text = (
            REPO_ROOT / '.github' / 'workflows' / 'ci.yml'
        ).read_text(encoding='utf-8')

        self.assertIn('coverage run -m unittest discover -s tests', workflow_text)
        self.assertIn('coverage report --show-missing', workflow_text)
        self.assertIn(
            'shellcheck scripts/bootstrap.sh scripts/install-python-deps.sh scripts/run-local.sh docker/entrypoint-run.sh docker/entrypoint-install.sh',
            workflow_text,
        )

    def test_repo_does_not_use_all_export_shims(self) -> None:
        forbidden_locations = []
        for path in (REPO_ROOT / 'openhands_agent').rglob('*.py'):
            text = path.read_text(encoding='utf-8')
            if '__all__ =' in text:
                forbidden_locations.append(str(path.relative_to(REPO_ROOT)))

        self.assertEqual(
            forbidden_locations,
            [],
            msg='remove __all__ export shims from: ' + ', '.join(forbidden_locations),
        )
