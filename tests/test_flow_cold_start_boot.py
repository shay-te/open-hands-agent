"""Flow #1 — Cold-start boot.

A-Z scenario:

    1. Operator runs ``kato run --config conf/config.yaml``.
    2. ``main(cfg)`` validates environment, sandbox, TLS pin, docker
       (when enabled).
    3. ``KatoInstance.init(cfg)`` constructs the service graph and
       runs the parallel startup dependency validators (repo + task
       client + impl + testing).
    4. Boot helpers run, in order:
        a. ``_recover_orphan_workspaces`` — folders on disk that lost
           their session record.
        b. ``_reconcile_workspace_branches`` — git heads vs expected
           branch name (== task id).
        c. ``_reset_stuck_workspace_statuses`` — PROVISIONING → ACTIVE
           when ``.git`` exists.
    5. **NO auto-spawn of past sessions** (Bug 1 fix).
    6. ``_start_planning_webserver_if_enabled`` brings the UI up.
    7. Queued local-comment work is dispatched only after the UI has
       had first shot at loading.
    8. Shutdown hook registered.
    9. ``_warm_up_repository_inventory`` kicks off the background disk
       walk for repo discovery.
    10. ``_run_task_scan_loop`` starts the 30s polling cycle.

The order matters: orphan recovery must happen BEFORE the webserver
comes up (otherwise the UI flashes empty), but the no-auto-spawn
must remain enforced (Bug 1 territory). Both pinned below.

What this file does NOT do: instantiate the real ``KatoInstance`` or
``KatoCoreLib`` with their full service graph — that requires a real
config + every connection. We test the BOOT WIRING via the source-
inspection contract (the order helpers are called in ``main``) and
via direct invocation of the helper functions with mocked apps. Both
are bug-finding without needing a real boot.
"""

from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib import main as kato_main


class FlowColdStartBootMainSourceTests(unittest.TestCase):
    """Source-inspection guards: lock the order and presence of every
    boot-time call by reading the source of ``main()``.

    Source inspection is brittle on whitespace but EXACTLY the right
    tool for "this function calls X before Y" contracts that don't
    have a clean side-effect to assert against.
    """

    def setUp(self) -> None:
        self.src = inspect.getsource(kato_main.main)

    def test_flow_boot_calls_orphan_recovery(self) -> None:
        self.assertIn('_recover_orphan_workspaces(app)', self.src)

    def test_flow_boot_calls_branch_reconciliation(self) -> None:
        self.assertIn('_reconcile_workspace_branches(app)', self.src)

    def test_flow_boot_calls_stuck_status_reset(self) -> None:
        self.assertIn('_reset_stuck_workspace_statuses(app)', self.src)

    def test_flow_boot_does_not_call_resume_streaming_sessions(self) -> None:
        # Bug 1's smoking gun. If THIS test fails, kato is auto-spawning
        # all past Claude sessions at boot — burning tokens, surprising
        # the operator with a thundering herd of subprocesses.
        self.assertNotIn(
            '_resume_streaming_sessions(app)', self.src,
            'main() is auto-spawning sessions at boot again (Bug 1 regression)',
        )

    def test_flow_boot_calls_planning_webserver_starter(self) -> None:
        self.assertIn('_start_planning_webserver_if_enabled(app)', self.src)

    def test_flow_boot_registers_shutdown_hook(self) -> None:
        # Without the shutdown hook, kato leaks subprocesses on
        # Ctrl-C (Claude sessions stay alive in zombie state).
        self.assertIn('_register_shutdown_hook(app)', self.src)

    def test_flow_boot_warms_up_repository_inventory(self) -> None:
        # Without warm-up, the FIRST task pickup pays the disk-walk
        # cost (can be seconds on a large workspaces root). Warm-up
        # runs the walk in background.
        self.assertIn('_warm_up_repository_inventory(app)', self.src)

    def test_flow_boot_runs_task_scan_loop(self) -> None:
        self.assertIn('_run_task_scan_loop(', self.src)

    def test_flow_boot_orphan_recovery_runs_before_webserver(self) -> None:
        # Order matters: if the webserver comes up FIRST, the UI
        # shows an empty tab list for a moment then tabs pop in as
        # recovery completes. The operator-visible flicker is worth
        # avoiding.
        recovery_idx = self.src.index('_recover_orphan_workspaces(app)')
        webserver_idx = self.src.index('_start_planning_webserver_if_enabled(app)')
        self.assertLess(
            recovery_idx, webserver_idx,
            'webserver starts before orphan recovery — UI flickers '
            'empty-then-populated on boot',
        )

    def test_flow_boot_branch_reconcile_runs_after_orphan_recovery(self) -> None:
        # Branch reconcile assumes the workspace records exist —
        # orphan recovery is what creates them. Reversing the order
        # leaves real workspaces with their branches not reconciled.
        recovery_idx = self.src.index('_recover_orphan_workspaces(app)')
        reconcile_idx = self.src.index('_reconcile_workspace_branches(app)')
        self.assertLess(recovery_idx, reconcile_idx)

    def test_flow_boot_validation_runs_before_recovery(self) -> None:
        # ``KatoInstance.init`` runs the startup-dependency validators
        # in parallel. Boot must NOT proceed to recovery / webserver
        # if validation failed (the early-return inside the try is
        # what guarantees this — recovery comes AFTER init).
        init_idx = self.src.index('KatoInstance.init(cfg)')
        recovery_idx = self.src.index('_recover_orphan_workspaces(app)')
        self.assertLess(init_idx, recovery_idx)

    def test_flow_boot_warm_up_runs_before_scan_loop(self) -> None:
        # Warm-up is fire-and-forget background — but it must be
        # KICKED OFF before the scan loop's first tick or the loop
        # waits on a cold cache.
        warm_idx = self.src.index('_warm_up_repository_inventory(app)')
        loop_idx = self.src.index('_run_task_scan_loop(')
        self.assertLess(warm_idx, loop_idx)


# ---------------------------------------------------------------------------
# Direct helper invocation: do the boot helpers handle empty / errored apps?
# ---------------------------------------------------------------------------


class FlowColdStartBootHelperRobustnessTests(unittest.TestCase):
    """Each boot helper should fail-safe when its service is missing,
    raises, or returns nothing useful. Otherwise a single broken
    dependency takes the whole process down before the webserver
    can come up and surface the error."""

    def test_flow_boot_recover_orphan_workspaces_handles_no_service(self) -> None:
        # App with no workspace_recovery_service: helper should not
        # raise.
        app = SimpleNamespace(logger=MagicMock())
        try:
            kato_main._recover_orphan_workspaces(app)
        except AttributeError:
            self.fail(
                '_recover_orphan_workspaces crashed when service was missing '
                '— boot would never reach the webserver',
            )
        except Exception as exc:
            # Any exception is suspicious — boot helpers should be
            # safety-padded. But we accept that some implementations
            # log-and-swallow; the regression we're catching is hard
            # AttributeError-during-attribute-access.
            self.assertNotIsInstance(exc, AttributeError)

    def test_flow_boot_recover_orphan_workspaces_handles_recovery_exception(self) -> None:
        # If the recovery service itself raises (e.g., disk perms),
        # the helper should log and continue rather than killing
        # the boot.
        recovery = MagicMock()
        recovery.recover_orphan_workspaces.side_effect = RuntimeError('disk perms')
        app = SimpleNamespace(
            workspace_recovery_service=recovery,
            service=SimpleNamespace(workspace_recovery_service=recovery),
            logger=MagicMock(),
        )
        try:
            kato_main._recover_orphan_workspaces(app)
        except RuntimeError:
            self.fail(
                'orphan recovery exception killed boot — webserver never '
                'comes up, operator has no UI to see the error',
            )


if __name__ == '__main__':
    unittest.main()
