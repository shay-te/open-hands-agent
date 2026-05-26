"""Coverage for ``KatoCoreLib`` static / builder methods."""

from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.kato_core_lib import KatoCoreLib, _EmailCoreLibProxy


class EmailCoreLibProxyTests(unittest.TestCase):
    """Lines 91-93: ``_EmailCoreLibProxy.__call__`` lazy-imports the
    real ``EmailCoreLib`` constructor when invoked."""

    def test_proxy_delegates_to_real_email_core_lib(self) -> None:
        proxy = _EmailCoreLibProxy()
        fake_module = MagicMock()
        fake_module.EmailCoreLib.return_value = 'real-email-lib-instance'
        with patch.dict(
            'sys.modules',
            {'email_core_lib.email_core_lib': fake_module},
            clear=False,
        ):
            result = proxy('cfg')
        fake_module.EmailCoreLib.assert_called_once_with('cfg')
        self.assertEqual(result, 'real-email-lib-instance')


class BuildSecurityScannerServiceTests(unittest.TestCase):
    """Lines 368-420: the scanner-config translation path. Builds a
    SecurityScannerService from ``scanner_cfg`` honoring enabled,
    block_on_severity, per-runner toggles, and timeout overrides."""

    def test_uses_default_when_scanner_cfg_missing(self) -> None:
        # Lines 362-367: ``if scanner_cfg is None`` → default.
        # MagicMock auto-creates attributes, so we use SimpleNamespace
        # to force the missing-attr path.
        open_cfg = SimpleNamespace()
        instance = KatoCoreLib.__new__(KatoCoreLib)
        result = instance._build_security_scanner_service(open_cfg)
        self.assertIsNotNone(result)

    def test_uses_default_severities_when_block_on_severity_absent(self) -> None:
        # ``block_on_severity is None`` → critical-only default.
        # Matches the YAML default: HIGH+ findings surface as warnings
        # but don't refuse the task (transitive-dep CVE noise on
        # routine codebases shouldn't be a hard gate).
        from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
            Severity,
        )
        scanner_cfg = SimpleNamespace(enabled=True, block_on_severity=None,
                                       runners=None, timeouts=None)
        open_cfg = SimpleNamespace(security_scanner=scanner_cfg)
        instance = KatoCoreLib.__new__(KatoCoreLib)
        service = instance._build_security_scanner_service(open_cfg)
        self.assertEqual(
            service._config.block_on_severity, (Severity.CRITICAL,),
        )

    def test_honors_explicit_block_on_severity_and_runner_toggles(self) -> None:
        # Lines 369-420: full traversal of the scanner-cfg branch.
        from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
            Severity,
        )
        scanner_cfg = SimpleNamespace(
            enabled=True,
            block_on_severity=['critical', 'high'],
            runners=SimpleNamespace(
                env_file=True,
                detect_secrets=False,  # disabled
                bandit=True,
                safety=False,
                npm_audit=False,
            ),
            timeouts=SimpleNamespace(
                secrets=10,
                dependencies=30,
                code_patterns=20,
            ),
        )
        open_cfg = SimpleNamespace(security_scanner=scanner_cfg)
        instance = KatoCoreLib.__new__(KatoCoreLib)
        service = instance._build_security_scanner_service(open_cfg)
        # Runner list was rebuilt to drop disabled ones.
        runner_names = [r.name for r in service._config.runners]
        self.assertNotIn('detect-secrets', runner_names)
        self.assertNotIn('safety', runner_names)
        self.assertIn('bandit', runner_names)
        # Block severities include CRITICAL + HIGH.
        self.assertIn(Severity.CRITICAL, service._config.block_on_severity)

    def test_skips_none_timeout_values_and_safety_fallback(self) -> None:
        """Covers branch 432->426 (None timeout skipped) and 435->439
        (no ``safety`` override → no ``npm-audit`` setdefault)."""
        # ``dependencies`` is None → the per-key ``if value is not None``
        # check skips it (432->426). ``secrets`` is also None so
        # ``safety`` never lands in timeout_overrides, exercising the
        # False branch of ``if 'safety' in timeout_overrides`` (435->439).
        scanner_cfg = SimpleNamespace(
            enabled=True,
            block_on_severity=None,
            runners=None,
            timeouts=SimpleNamespace(
                secrets=None,
                dependencies=None,
                code_patterns=15,
            ),
        )
        open_cfg = SimpleNamespace(security_scanner=scanner_cfg)
        instance = KatoCoreLib.__new__(KatoCoreLib)
        service = instance._build_security_scanner_service(open_cfg)
        # bandit (code_patterns) override took effect; safety/npm-audit
        # kept their defaults because nothing seeded them.
        timeouts_by_name = {
            r.name: r.timeout_seconds for r in service._config.runners
        }
        self.assertEqual(timeouts_by_name.get('bandit'), 15)


class BuildRuntimePostureSupplierTests(unittest.TestCase):
    """Lines 446-452: the supplier closure inspects the live scanner
    config to decide ``scanner_blocks_at_medium``."""

    def test_supplier_reads_scanner_blocks_at_medium(self) -> None:
        from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
            Severity,
        )
        scanner = MagicMock()
        scanner._config.block_on_severity = (Severity.MEDIUM, Severity.HIGH)
        supplier = KatoCoreLib._build_runtime_posture_supplier(
            security_scanner_service=scanner,
            bypass_permissions=True,
            docker_mode_on=False,
        )
        posture = supplier()
        self.assertTrue(posture.bypass_permissions)
        self.assertFalse(posture.docker_mode_on)
        self.assertTrue(posture.scanner_blocks_at_medium)

    def test_supplier_handles_missing_scanner(self) -> None:
        supplier = KatoCoreLib._build_runtime_posture_supplier(
            security_scanner_service=None,
            bypass_permissions=False,
            docker_mode_on=True,
        )
        posture = supplier()
        self.assertFalse(posture.scanner_blocks_at_medium)
        self.assertTrue(posture.docker_mode_on)

    def test_supplier_handles_scanner_without_config(self) -> None:
        """Covers branch 485->488: scanner present but ``_config`` is None."""
        scanner = SimpleNamespace(_config=None)
        supplier = KatoCoreLib._build_runtime_posture_supplier(
            security_scanner_service=scanner,
            bypass_permissions=False,
            docker_mode_on=False,
        )
        posture = supplier()
        self.assertFalse(posture.scanner_blocks_at_medium)


class ResolveTicketPlatformConfigTests(unittest.TestCase):
    """Line 484: raises when no per-platform config block is present."""

    def test_raises_when_platform_config_missing(self) -> None:
        # ``youtrack`` is the default platform — but no ``youtrack``
        # block on the open_cfg.
        open_cfg = SimpleNamespace(issue_platform='youtrack')
        with self.assertRaisesRegex(ValueError, 'missing issue platform config'):
            KatoCoreLib._resolve_ticket_platform_config(open_cfg)


class KickStartupCompactTests(unittest.TestCase):
    """Lines 541-554: background-thread compact + exception swallow."""

    def test_returns_early_when_no_compact_due(self) -> None:
        service = MagicMock()
        service.should_compact.return_value = False
        KatoCoreLib._kick_startup_compact(service)
        service.compact.assert_not_called()

    def test_spawns_background_thread_when_compact_due(self) -> None:
        service = MagicMock()
        service.should_compact.return_value = True
        # Make compact() block briefly so we can confirm it's threaded.
        compact_event = threading.Event()
        service.compact.side_effect = lambda: compact_event.set()
        KatoCoreLib._kick_startup_compact(service)
        # Wait briefly for the thread to fire.
        self.assertTrue(compact_event.wait(timeout=2.0))

    def test_background_thread_swallows_compact_exception(self) -> None:
        # Line 543-547: exception inside _run is logged + swallowed.
        service = MagicMock()
        service.should_compact.return_value = True
        compact_event = threading.Event()

        def boom():
            compact_event.set()
            raise RuntimeError('compact crashed')

        service.compact.side_effect = boom
        KatoCoreLib._kick_startup_compact(service)
        self.assertTrue(compact_event.wait(timeout=2.0))
        # Wait for the thread to finish (join via Thread enumerate).
        time.sleep(0.05)


class ValidateRuntimeSourceFingerprintTests(unittest.TestCase):
    """Line 563: raises when the fingerprint doesn't match."""

    def test_returns_silently_when_fingerprint_absent(self) -> None:
        # Defensive: no expected fingerprint configured → no check.
        instance = KatoCoreLib.__new__(KatoCoreLib)
        # Use a dict-like config so .get() works.
        instance._validate_runtime_source_fingerprint(
            SimpleNamespace(get=lambda k, d=None: ''),
        )

    def test_raises_when_fingerprint_mismatches(self) -> None:
        instance = KatoCoreLib.__new__(KatoCoreLib)
        with patch(
            'kato_core_lib.kato_core_lib.runtime_source_fingerprint',
            return_value='actual-fingerprint',
        ):
            with self.assertRaisesRegex(RuntimeError, 'fingerprint mismatch'):
                instance._validate_runtime_source_fingerprint(
                    SimpleNamespace(get=lambda k, d=None:
                                    'different-fingerprint'
                                    if k == 'source_fingerprint' else d),
                )

    def test_returns_silently_when_fingerprint_matches(self) -> None:
        instance = KatoCoreLib.__new__(KatoCoreLib)
        with patch(
            'kato_core_lib.kato_core_lib.runtime_source_fingerprint',
            return_value='match',
        ):
            # No raise.
            instance._validate_runtime_source_fingerprint(
                SimpleNamespace(get=lambda k, d=None:
                                'match' if k == 'source_fingerprint' else d),
            )


if __name__ == '__main__':
    unittest.main()
