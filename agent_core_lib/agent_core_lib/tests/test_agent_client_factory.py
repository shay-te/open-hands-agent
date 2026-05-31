"""Unit tests for the platform resolver + factory plumbing.

We don't construct real backends here (those have heavy
dependencies — Claude pulls in the streaming subprocess machinery,
OpenHands needs a live HTTP service). We exercise:

* AgentPlatform enum shape and values
* All alias permutations for resolve_platform
* AgentClientFactory.__init__ flag storage
* build() dispatch to correct builder
* _build_claude happy path (lazy-import mocked) and error path
* _build_openhands happy path (lazy-import mocked)
* AgentCoreLib composition root
* A-Z flow: resolve_platform → factory → build → provider protocol
"""

from __future__ import annotations

import types
import unittest
from unittest.mock import MagicMock, patch

from agent_provider_contracts.agent_provider_contracts.agent_provider import AgentProvider

from agent_core_lib.agent_core_lib.agent_core_lib import AgentCoreLib
from agent_core_lib.agent_core_lib.client.agent_client_factory import (
    AgentClientFactory,
    _PLATFORM_ALIASES,
    resolve_platform,
)
from agent_core_lib.agent_core_lib.platform import AgentPlatform


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_claude_cfg(**overrides):
    """Minimal duck-typed Claude config object."""
    ns = types.SimpleNamespace(
        binary='claude',
        model='claude-3-5-sonnet-20241022',
        max_turns=10,
        effort='normal',
        allowed_tools='',
        disallowed_tools='',
        bypass_permissions=False,
        timeout_seconds=1800,
        model_smoke_test_enabled=False,
        architecture_doc_path='',
        lessons_path='',
    )
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


def _make_open_cfg_for_claude(**claude_overrides):
    """Wraps a Claude config in the outer open_cfg envelope."""
    ns = types.SimpleNamespace(
        claude=_make_claude_cfg(**claude_overrides),
        repository_root_path='/repos',
    )
    return ns


def _make_codex_cfg(**overrides):
    """Minimal duck-typed Codex config object — same shape as Claude's."""
    ns = types.SimpleNamespace(
        binary='codex',
        model='codex-mini',
        max_turns=10,
        effort='medium',
        allowed_tools='',
        disallowed_tools='',
        bypass_permissions=False,
        timeout_seconds=1800,
        model_smoke_test_enabled=False,
        architecture_doc_path='',
        lessons_path='',
    )
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


def _make_open_cfg_for_codex(**codex_overrides):
    """Wraps a Codex config in the outer open_cfg envelope."""
    ns = types.SimpleNamespace(
        codex=_make_codex_cfg(**codex_overrides),
        repository_root_path='/repos',
    )
    return ns


class _OpenHandsCfg:
    """Supports both attribute access and dict-style .get() — mirrors
    OmegaConf DictConfig behaviour used in production."""

    def __init__(self, **kwargs):
        self._data = kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)

    def get(self, key, default=None):
        return self._data.get(key, default)


def _make_open_cfg_for_openhands(**kwargs):
    defaults = dict(
        base_url='http://localhost:3000',
        api_key='key-abc',
        testing_base_url='http://test:3000',
        testing_container_enabled=False,
        llm_model='gpt-4o',
        llm_api_key='llm-key',
        llm_base_url='',
        poll_interval_seconds=2.0,
        max_poll_attempts=900,
        model_smoke_test_enabled=False,
    )
    defaults.update(kwargs)
    oh_cfg = _OpenHandsCfg(**defaults)
    outer = types.SimpleNamespace(openhands=oh_cfg)
    return outer


def _make_compliant_backend():
    """Returns a duck-typed object satisfying AgentProvider."""

    class _Backend:
        def validate_connection(self): return None
        def validate_model_access(self): return None
        def implement_task(self, task, agent_session_id='', prepared_task=None): return {'success': True}
        def test_task(self, task, prepared_task=None): return {'success': True}
        def fix_review_comment(self, comment, branch_name, agent_session_id='', task_id='', task_summary=''): return {'success': True}
        def fix_review_comments(self, comments, branch_name, agent_session_id='', task_id='', task_summary='', mode='fix'): return {'success': True}
        def delete_conversation(self, conversation_id): return None
        def stop_all_conversations(self): return None

    return _Backend()


# ---------------------------------------------------------------------------
# AgentPlatform
# ---------------------------------------------------------------------------

class AgentPlatformTests(unittest.TestCase):
    def test_enum_has_exactly_three_members(self) -> None:
        members = list(AgentPlatform)
        self.assertEqual(len(members), 3, f'expected 3 members, got {members}')

    def test_enum_members_are_claude_codex_and_openhands(self) -> None:
        self.assertIn(AgentPlatform.CLAUDE, list(AgentPlatform))
        self.assertIn(AgentPlatform.CODEX, list(AgentPlatform))
        self.assertIn(AgentPlatform.OPENHANDS, list(AgentPlatform))

    def test_enum_values_are_lowercase_string_slugs(self) -> None:
        self.assertEqual(AgentPlatform.CLAUDE.value, 'claude')
        self.assertEqual(AgentPlatform.CODEX.value, 'codex')
        self.assertEqual(AgentPlatform.OPENHANDS.value, 'openhands')

    def test_enum_members_compare_by_identity(self) -> None:
        self.assertIs(AgentPlatform.CLAUDE, AgentPlatform.CLAUDE)
        self.assertIsNot(AgentPlatform.CLAUDE, AgentPlatform.OPENHANDS)
        self.assertIsNot(AgentPlatform.CODEX, AgentPlatform.CLAUDE)


# ---------------------------------------------------------------------------
# _PLATFORM_ALIASES
# ---------------------------------------------------------------------------

class PlatformAliasTableTests(unittest.TestCase):
    def test_every_alias_maps_to_a_known_platform(self) -> None:
        for alias, platform in _PLATFORM_ALIASES.items():
            self.assertIsInstance(platform, AgentPlatform, f'alias {alias!r} maps to {platform!r}')

    def test_claude_aliases_cover_expected_spellings(self) -> None:
        expected = {'claude', 'claude-code', 'claude_code', 'claude-cli', 'claude_cli'}
        actual = {k for k, v in _PLATFORM_ALIASES.items() if v == AgentPlatform.CLAUDE}
        self.assertEqual(actual, expected)

    def test_openhands_aliases_cover_expected_spellings(self) -> None:
        expected = {'openhands', 'open-hands', 'open_hands', ''}
        actual = {k for k, v in _PLATFORM_ALIASES.items() if v == AgentPlatform.OPENHANDS}
        self.assertEqual(actual, expected)

    def test_codex_aliases_cover_expected_spellings(self) -> None:
        expected = {'codex', 'codex-cli', 'codex_cli', 'openai-codex', 'openai_codex'}
        actual = {k for k, v in _PLATFORM_ALIASES.items() if v == AgentPlatform.CODEX}
        self.assertEqual(actual, expected)

    def test_empty_string_is_in_aliases_for_historical_compat(self) -> None:
        self.assertIn('', _PLATFORM_ALIASES)
        self.assertEqual(_PLATFORM_ALIASES[''], AgentPlatform.OPENHANDS)


# ---------------------------------------------------------------------------
# resolve_platform
# ---------------------------------------------------------------------------

class ResolvePlatformTests(unittest.TestCase):
    def test_canonical_names_map_one_to_one(self) -> None:
        self.assertEqual(resolve_platform('claude'), AgentPlatform.CLAUDE)
        self.assertEqual(resolve_platform('openhands'), AgentPlatform.OPENHANDS)

    def test_all_claude_alias_variants(self) -> None:
        for alias in ('claude', 'claude-code', 'claude_code', 'claude-cli', 'claude_cli'):
            self.assertEqual(resolve_platform(alias), AgentPlatform.CLAUDE, alias)

    def test_all_openhands_alias_variants(self) -> None:
        for alias in ('openhands', 'open-hands', 'open_hands'):
            self.assertEqual(resolve_platform(alias), AgentPlatform.OPENHANDS, alias)

    def test_blank_input_falls_back_to_openhands_for_historical_compat(self) -> None:
        self.assertEqual(resolve_platform(''), AgentPlatform.OPENHANDS)

    def test_unknown_backend_raises_with_actionable_message(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            resolve_platform('gpt-4')
        msg = str(ctx.exception)
        self.assertIn('gpt-4', msg)
        self.assertIn('claude', msg)
        self.assertIn('openhands', msg)

    def test_unknown_backend_message_includes_the_bad_value(self) -> None:
        # ``codex`` is now a supported backend, so use an unknown name that
        # is still firmly outside the alias table.
        with self.assertRaises(ValueError) as ctx:
            resolve_platform('gemini')
        self.assertIn('gemini', str(ctx.exception))

    def test_input_is_case_insensitive(self) -> None:
        self.assertEqual(resolve_platform('Claude'), AgentPlatform.CLAUDE)
        self.assertEqual(resolve_platform('CLAUDE'), AgentPlatform.CLAUDE)
        self.assertEqual(resolve_platform('OPENHANDS'), AgentPlatform.OPENHANDS)
        self.assertEqual(resolve_platform('OpenHands'), AgentPlatform.OPENHANDS)

    def test_input_strips_leading_trailing_whitespace(self) -> None:
        self.assertEqual(resolve_platform('  claude  '), AgentPlatform.CLAUDE)
        self.assertEqual(resolve_platform('\topenhands\n'), AgentPlatform.OPENHANDS)

    def test_none_like_empty_string_handled(self) -> None:
        # resolve_platform('') is the historical default — must not raise
        result = resolve_platform('')
        self.assertEqual(result, AgentPlatform.OPENHANDS)


# ---------------------------------------------------------------------------
# AgentClientFactory.__init__
# ---------------------------------------------------------------------------

class AgentClientFactoryInitTests(unittest.TestCase):
    def test_stores_max_retries(self) -> None:
        factory = AgentClientFactory(max_retries=5)
        self.assertEqual(factory._max_retries, 5)

    def test_default_flags_are_false(self) -> None:
        factory = AgentClientFactory(max_retries=1)
        self.assertFalse(factory._testing)
        self.assertFalse(factory._docker_mode_on)
        self.assertFalse(factory._read_only_tools_on)

    def test_testing_flag_is_stored(self) -> None:
        factory = AgentClientFactory(max_retries=1, testing=True)
        self.assertTrue(factory._testing)
        self.assertFalse(factory._docker_mode_on)

    def test_docker_mode_flag_is_stored(self) -> None:
        factory = AgentClientFactory(max_retries=1, docker_mode_on=True)
        self.assertTrue(factory._docker_mode_on)

    def test_read_only_tools_flag_is_stored(self) -> None:
        factory = AgentClientFactory(max_retries=1, read_only_tools_on=True)
        self.assertTrue(factory._read_only_tools_on)

    def test_all_flags_stored_together(self) -> None:
        factory = AgentClientFactory(
            max_retries=7,
            testing=True,
            docker_mode_on=True,
            read_only_tools_on=True,
        )
        self.assertEqual(factory._max_retries, 7)
        self.assertTrue(factory._testing)
        self.assertTrue(factory._docker_mode_on)
        self.assertTrue(factory._read_only_tools_on)


# ---------------------------------------------------------------------------
# AgentClientFactory.build dispatch
# ---------------------------------------------------------------------------

class FactoryDispatchTests(unittest.TestCase):
    def test_claude_dispatch_routes_to_claude_builder(self) -> None:
        factory = AgentClientFactory(max_retries=1)
        with patch.object(factory, '_build_claude', return_value='CLAUDE') as bc, \
             patch.object(factory, '_build_codex', return_value='CODEX') as bcx, \
             patch.object(factory, '_build_openhands', return_value='OH') as boh:
            result = factory.build(AgentPlatform.CLAUDE, object())
        self.assertEqual(result, 'CLAUDE')
        bc.assert_called_once()
        bcx.assert_not_called()
        boh.assert_not_called()

    def test_codex_dispatch_routes_to_codex_builder(self) -> None:
        factory = AgentClientFactory(max_retries=1)
        with patch.object(factory, '_build_claude', return_value='CLAUDE') as bc, \
             patch.object(factory, '_build_codex', return_value='CODEX') as bcx, \
             patch.object(factory, '_build_openhands', return_value='OH') as boh:
            result = factory.build(AgentPlatform.CODEX, object())
        self.assertEqual(result, 'CODEX')
        bcx.assert_called_once()
        bc.assert_not_called()
        boh.assert_not_called()

    def test_openhands_dispatch_routes_to_openhands_builder(self) -> None:
        factory = AgentClientFactory(max_retries=1)
        with patch.object(factory, '_build_claude', return_value='CLAUDE') as bc, \
             patch.object(factory, '_build_codex', return_value='CODEX') as bcx, \
             patch.object(factory, '_build_openhands', return_value='OH') as boh:
            result = factory.build(AgentPlatform.OPENHANDS, object())
        self.assertEqual(result, 'OH')
        boh.assert_called_once()
        bc.assert_not_called()
        bcx.assert_not_called()

    def test_unhandled_platform_raises_clearly(self) -> None:
        factory = AgentClientFactory(max_retries=1)

        class _FakePlatform:
            pass

        with self.assertRaises(ValueError):
            factory.build(_FakePlatform(), object())  # type: ignore[arg-type]

    def test_unhandled_platform_error_message_contains_the_value(self) -> None:
        factory = AgentClientFactory(max_retries=1)

        class _Fake:
            def __repr__(self): return '<FakePlatform>'

        with self.assertRaises(ValueError) as ctx:
            factory.build(_Fake(), object())  # type: ignore[arg-type]
        self.assertIn('FakePlatform', str(ctx.exception))


# ---------------------------------------------------------------------------
# AgentClientFactory._build_claude
# ---------------------------------------------------------------------------

class BuildClaudeTests(unittest.TestCase):
    def test_raises_when_claude_config_block_is_missing(self) -> None:
        factory = AgentClientFactory(max_retries=1)
        cfg_without_claude = types.SimpleNamespace()  # no .claude attribute
        with patch('claude_core_lib.claude_core_lib.cli_client.ClaudeCliClient'):
            with self.assertRaises(RuntimeError) as ctx:
                factory._build_claude(cfg_without_claude)
        self.assertIn('claude', str(ctx.exception).lower())

    def test_raises_when_claude_config_is_explicitly_none(self) -> None:
        factory = AgentClientFactory(max_retries=1)
        cfg = types.SimpleNamespace(claude=None)
        with patch('claude_core_lib.claude_core_lib.cli_client.ClaudeCliClient'):
            with self.assertRaises(RuntimeError):
                factory._build_claude(cfg)

    def test_instantiates_claude_cli_client_with_correct_params(self) -> None:
        factory = AgentClientFactory(max_retries=3, docker_mode_on=True, read_only_tools_on=True)
        cfg = _make_open_cfg_for_claude(
            binary='/usr/bin/claude',
            model='claude-opus-4',
            max_turns=20,
            effort='high',
            allowed_tools='Read,Write',
            disallowed_tools='Bash',
            bypass_permissions=True,
            timeout_seconds=900,
            model_smoke_test_enabled=False,
            architecture_doc_path='/arch.md',
            lessons_path='/lessons.md',
        )
        cfg.repository_root_path = '/repos/project'

        mock_client = MagicMock()
        with patch(
            'claude_core_lib.claude_core_lib.cli_client.ClaudeCliClient',
            return_value=mock_client,
        ) as MockCls:
            result = factory._build_claude(cfg)

        MockCls.assert_called_once_with(
            binary='/usr/bin/claude',
            model='claude-opus-4',
            max_turns=20,
            effort='high',
            allowed_tools='Read,Write',
            disallowed_tools='Bash',
            bypass_permissions=True,
            docker_mode_on=True,
            read_only_tools_on=True,
            timeout_seconds=900,
            max_retries=3,
            repository_root_path='/repos/project',
            model_smoke_test_enabled=False,
            architecture_doc_path='/arch.md',
            lessons_path='/lessons.md',
            workspace_refusal_guidance='',
        )
        self.assertIs(result, mock_client)

    def test_testing_flag_suppresses_model_smoke_test(self) -> None:
        factory = AgentClientFactory(max_retries=1, testing=True)
        cfg = _make_open_cfg_for_claude(model_smoke_test_enabled=True)

        with patch(
            'claude_core_lib.claude_core_lib.cli_client.ClaudeCliClient',
            return_value=MagicMock(),
        ) as MockCls:
            factory._build_claude(cfg)

        _, kwargs = MockCls.call_args
        # testing=True AND model_smoke_test_enabled=True → still False
        self.assertFalse(kwargs['model_smoke_test_enabled'])

    def test_non_testing_with_smoke_test_enabled_passes_true(self) -> None:
        factory = AgentClientFactory(max_retries=1, testing=False)
        cfg = _make_open_cfg_for_claude(model_smoke_test_enabled=True)

        with patch(
            'claude_core_lib.claude_core_lib.cli_client.ClaudeCliClient',
            return_value=MagicMock(),
        ) as MockCls:
            factory._build_claude(cfg)

        _, kwargs = MockCls.call_args
        self.assertTrue(kwargs['model_smoke_test_enabled'])

    def test_missing_optional_cfg_fields_use_safe_defaults(self) -> None:
        factory = AgentClientFactory(max_retries=2)
        # Minimal cfg — only the required 'claude' block, no other fields
        cfg = types.SimpleNamespace(claude=types.SimpleNamespace())

        with patch(
            'claude_core_lib.claude_core_lib.cli_client.ClaudeCliClient',
            return_value=MagicMock(),
        ) as MockCls:
            factory._build_claude(cfg)

        _, kwargs = MockCls.call_args
        self.assertEqual(kwargs['binary'], '')
        self.assertEqual(kwargs['model'], '')
        self.assertIsNone(kwargs['max_turns'])
        self.assertEqual(kwargs['timeout_seconds'], 1800)
        self.assertEqual(kwargs['repository_root_path'], '')


# ---------------------------------------------------------------------------
# AgentClientFactory._build_codex
# ---------------------------------------------------------------------------

class BuildCodexTests(unittest.TestCase):
    """Mirror of ``BuildClaudeTests`` — same behaviour, codex backend.

    Pins the symmetry contract: both CLI backends share the same
    config-block validation, same forwarding rules, same testing /
    smoke-test interaction. If a future refactor diverges them
    accidentally, these tests fire alongside the Claude tests.
    """

    def test_raises_when_codex_config_block_is_missing(self) -> None:
        factory = AgentClientFactory(max_retries=1)
        cfg_without_codex = types.SimpleNamespace()  # no .codex attribute
        with patch('codex_core_lib.codex_core_lib.cli_client.CodexCliClient'):
            with self.assertRaises(RuntimeError) as ctx:
                factory._build_codex(cfg_without_codex)
        self.assertIn('codex', str(ctx.exception).lower())

    def test_raises_when_codex_config_is_explicitly_none(self) -> None:
        factory = AgentClientFactory(max_retries=1)
        cfg = types.SimpleNamespace(codex=None)
        with patch('codex_core_lib.codex_core_lib.cli_client.CodexCliClient'):
            with self.assertRaises(RuntimeError):
                factory._build_codex(cfg)

    def test_instantiates_codex_cli_client_with_correct_params(self) -> None:
        factory = AgentClientFactory(max_retries=3, docker_mode_on=True, read_only_tools_on=True)
        cfg = _make_open_cfg_for_codex(
            binary='/usr/bin/codex',
            model='codex-large',
            max_turns=20,
            effort='high',
            allowed_tools='read,write',
            disallowed_tools='shell',
            bypass_permissions=True,
            timeout_seconds=900,
            model_smoke_test_enabled=False,
            architecture_doc_path='/arch.md',
            lessons_path='/lessons.md',
        )
        cfg.repository_root_path = '/repos/project'

        mock_client = MagicMock()
        with patch(
            'codex_core_lib.codex_core_lib.cli_client.CodexCliClient',
            return_value=mock_client,
        ) as MockCls:
            result = factory._build_codex(cfg)

        MockCls.assert_called_once_with(
            binary='/usr/bin/codex',
            model='codex-large',
            max_turns=20,
            effort='high',
            allowed_tools='read,write',
            disallowed_tools='shell',
            bypass_permissions=True,
            docker_mode_on=True,
            read_only_tools_on=True,
            timeout_seconds=900,
            max_retries=3,
            repository_root_path='/repos/project',
            model_smoke_test_enabled=False,
            architecture_doc_path='/arch.md',
            lessons_path='/lessons.md',
            workspace_refusal_guidance='',
        )
        self.assertIs(result, mock_client)

    def test_testing_flag_suppresses_model_smoke_test(self) -> None:
        factory = AgentClientFactory(max_retries=1, testing=True)
        cfg = _make_open_cfg_for_codex(model_smoke_test_enabled=True)

        with patch(
            'codex_core_lib.codex_core_lib.cli_client.CodexCliClient',
            return_value=MagicMock(),
        ) as MockCls:
            factory._build_codex(cfg)

        _, kwargs = MockCls.call_args
        # testing=True AND model_smoke_test_enabled=True → still False
        self.assertFalse(kwargs['model_smoke_test_enabled'])

    def test_non_testing_with_smoke_test_enabled_passes_true(self) -> None:
        factory = AgentClientFactory(max_retries=1, testing=False)
        cfg = _make_open_cfg_for_codex(model_smoke_test_enabled=True)

        with patch(
            'codex_core_lib.codex_core_lib.cli_client.CodexCliClient',
            return_value=MagicMock(),
        ) as MockCls:
            factory._build_codex(cfg)

        _, kwargs = MockCls.call_args
        self.assertTrue(kwargs['model_smoke_test_enabled'])

    def test_missing_optional_cfg_fields_use_safe_defaults(self) -> None:
        # Operator config block exists but has none of the optional
        # knobs set — the factory should fall back to documented defaults
        # rather than crash with AttributeError.
        factory = AgentClientFactory(max_retries=1)
        cfg = types.SimpleNamespace(
            codex=types.SimpleNamespace(),  # empty namespace
            repository_root_path='',
        )

        with patch(
            'codex_core_lib.codex_core_lib.cli_client.CodexCliClient',
            return_value=MagicMock(),
        ) as MockCls:
            factory._build_codex(cfg)

        _, kwargs = MockCls.call_args
        self.assertEqual(kwargs['binary'], '')
        self.assertEqual(kwargs['model'], '')
        self.assertIsNone(kwargs['max_turns'])
        self.assertEqual(kwargs['timeout_seconds'], 1800)
        self.assertEqual(kwargs['repository_root_path'], '')


# ---------------------------------------------------------------------------
# AgentClientFactory._build_openhands
# ---------------------------------------------------------------------------

class BuildOpenHandsTests(unittest.TestCase):
    def _patch_openhands(self, factory, cfg, *, base_url='http://oh:3000', llm_settings=None):
        mock_client = MagicMock()
        if llm_settings is None:
            llm_settings = {'model': 'gpt-4o', 'api_key': 'k'}
        with patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient',
            return_value=mock_client,
        ) as MockClient, patch(
            'openhands_core_lib.openhands_core_lib.config_utils.resolved_openhands_base_url',
            return_value=base_url,
        ), patch(
            'openhands_core_lib.openhands_core_lib.config_utils.resolved_openhands_llm_settings',
            return_value=llm_settings,
        ):
            result = factory._build_openhands(cfg)
        return result, MockClient

    def test_instantiates_openhands_client_with_correct_positional_args(self) -> None:
        factory = AgentClientFactory(max_retries=4)
        cfg = _make_open_cfg_for_openhands(api_key='my-api-key')
        llm = {'model': 'gpt-4o', 'api_key': 'llm-k'}

        result, MockClient = self._patch_openhands(
            factory, cfg, base_url='http://oh:3001', llm_settings=llm,
        )
        args, kwargs = MockClient.call_args
        self.assertEqual(args[0], 'http://oh:3001')
        self.assertEqual(args[1], 'my-api-key')
        self.assertEqual(args[2], 4)
        self.assertEqual(kwargs['llm_settings'], llm)

    def test_poll_interval_and_max_attempts_forwarded(self) -> None:
        factory = AgentClientFactory(max_retries=1)
        cfg = _make_open_cfg_for_openhands(
            poll_interval_seconds=5.0,
            max_poll_attempts=100,
        )

        _, MockClient = self._patch_openhands(factory, cfg)
        _, kwargs = MockClient.call_args
        self.assertEqual(kwargs['poll_interval_seconds'], 5.0)
        self.assertEqual(kwargs['max_poll_attempts'], 100)

    def test_testing_flag_suppresses_model_smoke_test(self) -> None:
        factory = AgentClientFactory(max_retries=1, testing=True)
        cfg = _make_open_cfg_for_openhands(model_smoke_test_enabled=True)

        _, MockClient = self._patch_openhands(factory, cfg)
        _, kwargs = MockClient.call_args
        self.assertFalse(kwargs['model_smoke_test_enabled'])

    def test_non_testing_with_smoke_test_enabled_passes_true(self) -> None:
        factory = AgentClientFactory(max_retries=1, testing=False)
        cfg = _make_open_cfg_for_openhands(model_smoke_test_enabled=True)

        _, MockClient = self._patch_openhands(factory, cfg)
        _, kwargs = MockClient.call_args
        self.assertTrue(kwargs['model_smoke_test_enabled'])

    def test_default_poll_values_used_when_not_in_cfg(self) -> None:
        factory = AgentClientFactory(max_retries=1)
        # No poll_interval_seconds or max_poll_attempts in cfg
        cfg = _make_open_cfg_for_openhands()
        # Remove them from the underlying dict so .get() returns defaults
        cfg.openhands._data.pop('poll_interval_seconds', None)
        cfg.openhands._data.pop('max_poll_attempts', None)

        _, MockClient = self._patch_openhands(factory, cfg)
        _, kwargs = MockClient.call_args
        self.assertAlmostEqual(kwargs['poll_interval_seconds'], 2.0)
        self.assertEqual(kwargs['max_poll_attempts'], 900)


# ---------------------------------------------------------------------------
# AgentCoreLib composition root
# ---------------------------------------------------------------------------

class AgentCoreLibTests(unittest.TestCase):
    def _build_core_lib(self, platform=AgentPlatform.CLAUDE, **factory_flags) -> AgentCoreLib:
        backend = _make_compliant_backend()
        with patch.object(AgentClientFactory, 'build', return_value=backend):
            lib = AgentCoreLib(
                platform=platform,
                cfg=object(),
                max_retries=1,
                **factory_flags,
            )
        return lib, backend

    def test_agent_attribute_is_set_on_construction(self) -> None:
        lib, backend = self._build_core_lib()
        self.assertIs(lib.agent, backend)

    def test_agent_satisfies_agent_provider_protocol(self) -> None:
        lib, _ = self._build_core_lib()
        self.assertIsInstance(lib.agent, AgentProvider)

    def test_flags_forwarded_to_factory(self) -> None:
        captured = {}

        original_init = AgentClientFactory.__init__

        def spy_init(self_f, *, max_retries, testing=False, docker_mode_on=False,
                     read_only_tools_on=False, workspace_refusal_guidance=''):
            captured['max_retries'] = max_retries
            captured['testing'] = testing
            captured['docker_mode_on'] = docker_mode_on
            captured['read_only_tools_on'] = read_only_tools_on
            captured['workspace_refusal_guidance'] = workspace_refusal_guidance
            original_init(
                self_f,
                max_retries=max_retries,
                testing=testing,
                docker_mode_on=docker_mode_on,
                read_only_tools_on=read_only_tools_on,
                workspace_refusal_guidance=workspace_refusal_guidance,
            )

        with patch.object(AgentClientFactory, '__init__', spy_init), \
             patch.object(AgentClientFactory, 'build', return_value=_make_compliant_backend()):
            AgentCoreLib(
                platform=AgentPlatform.OPENHANDS,
                cfg=object(),
                max_retries=9,
                testing=True,
                docker_mode_on=True,
                read_only_tools_on=True,
            )

        self.assertEqual(captured['max_retries'], 9)
        self.assertTrue(captured['testing'])
        self.assertTrue(captured['docker_mode_on'])
        self.assertTrue(captured['read_only_tools_on'])

    def test_agent_supports_all_eight_provider_operations(self) -> None:
        lib, backend = self._build_core_lib()
        required = {
            'validate_connection', 'validate_model_access',
            'implement_task', 'test_task',
            'fix_review_comment', 'fix_review_comments',
            'delete_conversation', 'stop_all_conversations',
        }
        for method in required:
            self.assertTrue(
                callable(getattr(lib.agent, method, None)),
                f'lib.agent missing {method}',
            )


# ---------------------------------------------------------------------------
# A-Z flow tests
# ---------------------------------------------------------------------------

class FlowTests(unittest.TestCase):
    """End-to-end flows that exercise the full chain without heavy deps."""

    def test_claude_flow_resolve_build_use(self) -> None:
        # A-Z: operator string → resolve → factory → build → call the backend

        # Step 1: resolve the operator-supplied string
        platform = resolve_platform('claude-code')
        self.assertEqual(platform, AgentPlatform.CLAUDE)

        # Step 2: construct the factory with runtime flags
        factory = AgentClientFactory(
            max_retries=2,
            testing=True,
            docker_mode_on=False,
            read_only_tools_on=True,
        )

        # Step 3: build the backend (lazy import mocked)
        cfg = _make_open_cfg_for_claude()
        mock_client = _make_compliant_backend()
        with patch(
            'claude_core_lib.claude_core_lib.cli_client.ClaudeCliClient',
            return_value=mock_client,
        ):
            agent = factory.build(platform, cfg)

        # Step 4: verify the returned object satisfies the Protocol
        self.assertIsInstance(agent, AgentProvider)

        # Step 5: call every operation through the agent
        agent.validate_connection()
        agent.validate_model_access()
        agent.implement_task(object())
        agent.test_task(object())
        agent.fix_review_comment(object(), 'main')
        agent.fix_review_comments([object()], 'main')
        agent.delete_conversation('conv-1')
        agent.stop_all_conversations()

    def test_openhands_flow_resolve_build_use(self) -> None:
        # A-Z: operator string → resolve → factory → build → call the backend

        platform = resolve_platform('open-hands')
        self.assertEqual(platform, AgentPlatform.OPENHANDS)

        factory = AgentClientFactory(max_retries=3, testing=True)

        cfg = _make_open_cfg_for_openhands()
        mock_client = _make_compliant_backend()
        with patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient',
            return_value=mock_client,
        ), patch(
            'openhands_core_lib.openhands_core_lib.config_utils.resolved_openhands_base_url',
            return_value='http://oh:3000',
        ), patch(
            'openhands_core_lib.openhands_core_lib.config_utils.resolved_openhands_llm_settings',
            return_value={},
        ):
            agent = factory.build(platform, cfg)

        self.assertIsInstance(agent, AgentProvider)

        result = agent.implement_task(object())
        self.assertTrue(result.get('success'))

    def test_all_platform_aliases_produce_a_working_factory_chain(self) -> None:
        # Every supported alias should resolve → build without error.
        all_aliases = list(_PLATFORM_ALIASES.keys())
        for alias in all_aliases:
            platform = resolve_platform(alias)
            factory = AgentClientFactory(max_retries=1, testing=True)
            backend = _make_compliant_backend()
            with patch.object(factory, '_build_claude', return_value=backend), \
                 patch.object(factory, '_build_codex', return_value=backend), \
                 patch.object(factory, '_build_openhands', return_value=backend):
                agent = factory.build(platform, object())
            self.assertIsInstance(
                agent, AgentProvider,
                f'alias {alias!r} did not produce an AgentProvider',
            )

    def test_build_openhands_raises_runtimeerror_when_openhands_block_missing(self) -> None:
        # Adversarial regression test (Bug-hunt finding):
        # ``_build_claude`` correctly raises a clear ``RuntimeError``
        # when the ``claude`` config block is missing (line 87-92).
        # ``_build_openhands`` does ``open_cfg.openhands`` directly
        # (line 127), so a missing block raises a cryptic AttributeError
        # instead of the operator-actionable RuntimeError.
        #
        # Symmetry contract: both backends MUST raise RuntimeError with
        # an actionable message when their config block is missing.
        factory = AgentClientFactory(max_retries=1)
        cfg_without_openhands = types.SimpleNamespace()  # no .openhands
        with patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient',
        ):
            with self.assertRaises(RuntimeError) as ctx:
                factory._build_openhands(cfg_without_openhands)
        # Operator-actionable message must reference "openhands" so the
        # operator can find the missing config block.
        self.assertIn('openhands', str(ctx.exception).lower())

    def test_build_openhands_raises_runtimeerror_when_openhands_is_none(self) -> None:
        # Symmetric to the Claude path's ``test_raises_when_claude_config_is_explicitly_none``.
        factory = AgentClientFactory(max_retries=1)
        cfg = types.SimpleNamespace(openhands=None)
        with patch(
            'openhands_core_lib.openhands_core_lib.openhands_client.OpenHandsClient',
        ):
            with self.assertRaises(RuntimeError):
                factory._build_openhands(cfg)

    def test_core_lib_composition_end_to_end(self) -> None:
        # resolve_platform → AgentCoreLib → .agent is usable
        platform = resolve_platform('claude')
        backend = _make_compliant_backend()
        with patch.object(AgentClientFactory, 'build', return_value=backend):
            lib = AgentCoreLib(
                platform=platform,
                cfg=object(),
                max_retries=1,
                testing=True,
            )

        self.assertIsInstance(lib.agent, AgentProvider)
        result = lib.agent.fix_review_comments([object()], 'feat/x', mode='answer')
        self.assertTrue(result['success'])


if __name__ == '__main__':
    unittest.main()
