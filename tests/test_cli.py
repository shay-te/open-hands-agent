"""Routing tests for the ``kato`` CLI (kato_core_lib/cli.py).

The CLI is a thin dispatcher: every subcommand shells out to the same
cross-platform implementation the Makefile used. We assert the argv it
builds for each subcommand (no real subprocess runs), the argparse
surface, and the compose-docker profile logic.
"""

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from kato_core_lib import cli


def _call_argv(mock_call):
    """First positional arg (the command list) of the first call."""
    return mock_call.call_args_list[0].args[0]


class SubcommandRoutingTests(unittest.TestCase):
    def setUp(self):
        # Deterministic interpreter paths so assertions don't depend on
        # whether a .venv exists on the test machine.
        patcher = mock.patch.object(cli, '_venv_python', return_value='VPY')
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_up_runs_run_local_script(self):
        with mock.patch.object(cli.subprocess, 'call', return_value=0) as m:
            self.assertEqual(cli.main(['up']), 0)
        argv = _call_argv(m)
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith('scripts/run_local.py'))

    def test_bootstrap_runs_bootstrap_script(self):
        with mock.patch.object(cli.subprocess, 'call', return_value=0) as m:
            cli.main(['bootstrap'])
        self.assertTrue(_call_argv(m)[1].endswith('scripts/bootstrap.py'))

    def test_configure_uses_venv_and_generate_env(self):
        with mock.patch.object(cli.subprocess, 'call', return_value=0) as m:
            cli.main(['configure'])
        argv = _call_argv(m)
        self.assertEqual(argv[0], 'VPY')
        self.assertTrue(argv[1].endswith('scripts/generate_env.py'))
        self.assertEqual(argv[2:], ['--output', '.env'])

    def test_doctor_defaults_to_all_mode(self):
        with mock.patch.object(cli.subprocess, 'call', return_value=0) as m:
            cli.main(['doctor'])
        self.assertEqual(
            _call_argv(m),
            ['VPY', '-m', 'kato_core_lib.validate_env',
             '--env-file', '.env', '--mode', 'all'],
        )

    def test_doctor_mode_is_forwarded(self):
        with mock.patch.object(cli.subprocess, 'call', return_value=0) as m:
            cli.main(['doctor', '--mode', 'openhands'])
        self.assertEqual(_call_argv(m)[-1], 'openhands')

    def test_doctor_rejects_unknown_mode(self):
        with self.assertRaises(SystemExit):
            cli.main(['doctor', '--mode', 'bogus'])

    def test_test_runs_unittest_discover(self):
        with mock.patch.object(cli.subprocess, 'call', return_value=0) as m:
            cli.main(['test'])
        self.assertEqual(
            _call_argv(m),
            ['VPY', '-m', 'unittest', 'discover', '-s', 'tests'],
        )

    def test_build_agent_server_tag_from_env(self):
        with mock.patch.dict('os.environ',
                             {'KATO_AGENT_SERVER_IMAGE_TAG': '9.9-test'}), \
             mock.patch.object(cli.subprocess, 'call', return_value=0) as m:
            cli.main(['build-agent-server'])
        argv = _call_argv(m)
        self.assertEqual(argv[:4],
                         ['docker', 'build', '-t', 'kato-agent-server:9.9-test'])

    def test_build_agent_server_default_tag(self):
        with mock.patch.dict('os.environ', {}, clear=True), \
             mock.patch.object(cli.subprocess, 'call', return_value=0) as m:
            cli.main(['build-agent-server'])
        self.assertEqual(_call_argv(m)[3], 'kato-agent-server:1.12.0-python')

    def test_sandbox_verify_runs_verify_module(self):
        with mock.patch.object(cli.subprocess, 'call', return_value=0) as m:
            cli.main(['sandbox', 'verify'])
        self.assertEqual(_call_argv(m),
                         ['VPY', '-m', 'kato_core_lib.sandbox.verify'])

    def test_sandbox_build_invokes_build_image(self):
        with mock.patch.object(cli.subprocess, 'call', return_value=0) as m:
            cli.main(['sandbox', 'build'])
        argv = _call_argv(m)
        self.assertEqual(argv[0], 'VPY')
        self.assertEqual(argv[1], '-c')
        self.assertIn('build_image()', argv[2])

    def test_sandbox_login_invokes_login_command(self):
        with mock.patch.object(cli.subprocess, 'call', return_value=0) as m:
            cli.main(['sandbox', 'login'])
        self.assertIn('login_command()', _call_argv(m)[2])

    def test_sandbox_rejects_unknown_action(self):
        with self.assertRaises(SystemExit):
            cli.main(['sandbox', 'nope'])

    def test_no_subcommand_errors(self):
        with self.assertRaises(SystemExit):
            cli.main([])

    def test_propagates_nonzero_exit_code(self):
        with mock.patch.object(cli.subprocess, 'call', return_value=3):
            self.assertEqual(cli.main(['up']), 3)


class ComposeDockerTests(unittest.TestCase):
    def setUp(self):
        fp = mock.patch.object(
            cli.subprocess, 'run',
            side_effect=[
                SimpleNamespace(stdout='fp123\n'),   # fingerprint
                SimpleNamespace(stdout='cID\n'),      # compose ps -q kato
            ],
        )
        self.addCleanup(fp.stop)
        self.run_mock = fp.start()

    def test_claude_backend_omits_openhands_profile(self):
        with mock.patch.object(cli, '_load_env',
                               return_value={'KATO_AGENT_BACKEND': 'claude'}), \
             mock.patch.dict('os.environ', {}, clear=True), \
             mock.patch.object(cli.subprocess, 'call', return_value=0) as m:
            self.assertEqual(cli.main(['compose-docker']), 0)
        up_argv = m.call_args_list[0].args[0]
        self.assertNotIn('--profile', up_argv)
        self.assertEqual(up_argv, ['docker', 'compose', 'up', '--build', '-d'])

    def test_openhands_backend_adds_profile_and_testing(self):
        env = {
            'KATO_AGENT_BACKEND': 'openhands',
            'OPENHANDS_SKIP_TESTING': 'false',
            'OPENHANDS_TESTING_CONTAINER_ENABLED': 'true',
        }
        with mock.patch.object(cli, '_load_env', return_value=env), \
             mock.patch.dict('os.environ', {}, clear=True), \
             mock.patch.object(cli.subprocess, 'call', return_value=0) as m:
            cli.main(['compose-docker'])
        up_argv = m.call_args_list[0].args[0]
        self.assertIn('openhands', up_argv)
        self.assertIn('testing', up_argv)

    def test_missing_container_id_is_an_error(self):
        # Re-stub run so ``ps -q kato`` returns empty.
        self.run_mock.side_effect = [
            SimpleNamespace(stdout='fp\n'),
            SimpleNamespace(stdout='   \n'),
        ]
        with mock.patch.object(cli, '_load_env', return_value={}), \
             mock.patch.dict('os.environ', {}, clear=True), \
             mock.patch.object(cli.subprocess, 'call', return_value=0):
            self.assertEqual(cli.main(['compose-docker']), 1)

    def test_docker_compose_up_failure_short_circuits(self):
        # cli.py line 150: when ``docker compose up --build -d`` returns
        # non-zero, return that exit code without trying to attach.
        with mock.patch.object(cli, '_load_env', return_value={}), \
             mock.patch.dict('os.environ', {}, clear=True), \
             mock.patch.object(cli.subprocess, 'call', return_value=2) as call_mock:
            rc = cli.main(['compose-docker'])
        self.assertEqual(rc, 2)
        # Only the up call should have happened.
        self.assertEqual(call_mock.call_count, 1)


class VenvPythonTests(unittest.TestCase):
    """Cover the venv-discovery branches in ``_venv_python``."""

    def test_returns_venv_python_when_present_posix(self):
        with mock.patch.object(cli, 'os', SimpleNamespace(name='posix')), \
             mock.patch.object(cli.Path, 'exists', return_value=True):
            result = cli._venv_python()
        self.assertTrue(result.endswith('/.venv/bin/python'))

    def test_returns_venv_python_windows_branch(self):
        with mock.patch.object(cli, 'os', SimpleNamespace(name='nt')), \
             mock.patch.object(cli.Path, 'exists', return_value=True):
            result = cli._venv_python()
        self.assertTrue(result.endswith('python.exe'))
        self.assertIn('Scripts', result)

    def test_falls_back_to_sys_executable_when_venv_missing(self):
        with mock.patch.object(cli, 'os', SimpleNamespace(name='posix')), \
             mock.patch.object(cli.Path, 'exists', return_value=False):
            result = cli._venv_python()
        self.assertEqual(result, sys.executable)


class LoadEnvTests(unittest.TestCase):
    """Cover ``_load_env``'s parsing edge cases."""

    def test_returns_empty_for_missing_file(self):
        # Line 63: ``if not path.exists(): return out`` early-return.
        from pathlib import Path
        result = cli._load_env(Path('/no/such/path/.env'))
        self.assertEqual(result, {})

    def test_parses_kv_pairs_and_strips_quotes(self):
        # Lines 64-75: real file with mixed shapes — blanks,
        # comments, unquoted, single-quoted, double-quoted, missing key.
        import tempfile
        from pathlib import Path
        with tempfile.NamedTemporaryFile(
            'w', suffix='.env', delete=False, encoding='utf-8',
        ) as fh:
            fh.write(
                '# comment line\n'
                '\n'
                'NOT_A_PAIR\n'
                'A=plain\n'
                'B="double quoted"\n'
                "C='single quoted'\n"
                '=missing_key\n'
            )
            env_path = Path(fh.name)
        try:
            out = cli._load_env(env_path)
        finally:
            env_path.unlink()
        self.assertEqual(out['A'], 'plain')
        self.assertEqual(out['B'], 'double quoted')
        self.assertEqual(out['C'], 'single quoted')
        self.assertNotIn('', out)


class ScriptEntryPointTest(unittest.TestCase):
    def test_main_module_invocation_raises_system_exit(self):
        # cli.py line 199: ``if __name__ == '__main__': raise SystemExit(main())``
        # runpy.run_module(..., run_name='__main__') re-executes the file in
        # a fresh namespace, so patching kato_core_lib.cli.cmd_doctor has no
        # effect on the new __main__ namespace's cmd_doctor. Patch the
        # external subprocess boundary instead — that's what every cmd_*
        # eventually calls through _run, and patches there survive runpy.
        import runpy
        argv_backup = sys.argv
        sys.argv = ['cli', 'doctor']
        try:
            with mock.patch('subprocess.call', return_value=0):
                with self.assertRaises(SystemExit) as ctx:
                    runpy.run_module('kato_core_lib.cli', run_name='__main__')
                self.assertEqual(ctx.exception.code, 0)
        finally:
            sys.argv = argv_backup


if __name__ == '__main__':
    unittest.main()
