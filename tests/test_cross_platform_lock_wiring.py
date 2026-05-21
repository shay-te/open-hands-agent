"""Cross-platform file-lock wiring tests.

Honest scope: this machine is macOS, so the ``msvcrt`` branch of
``_process_safe_write_lock`` can only be EXERCISED on Windows. What
we CAN verify on POSIX:

  1. The module imports cleanly with ``fcntl`` present and
     ``msvcrt`` absent (POSIX's actual environment).
  2. The lock helper falls through cleanly when BOTH module
     references are None (the no-op degradation path — wouldn't
     happen on real Windows or POSIX, but is the safety net for
     any future platform the code is dropped on).
  3. Forcing the module into "Windows-shape" (``fcntl=None`` +
     ``msvcrt=<stand-in>``) routes a real call into the
     msvcrt-shaped code path. The stand-in is a concrete recording
     class (NOT MagicMock) that mirrors the ``locking`` /
     ``LK_LOCK`` / ``LK_UNLCK`` API.

What this does NOT prove:
  - Real Windows ``msvcrt.locking`` semantics (we never run it).
  - Cross-process safety on Windows. Code-reviewed only.
"""

from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.data_layers.service import repository_approval_service
from kato_core_lib.comment_core_lib import comment_store


class _RecordingMsvcrt(object):
    """Concrete stand-in for the ``msvcrt`` module — records calls.

    Matches the surface ``_process_safe_write_lock`` uses:
      * ``LK_LOCK`` / ``LK_UNLCK`` int constants
      * ``locking(fileno, mode, nbytes)`` callable
    """

    LK_LOCK = 1
    LK_UNLCK = 2

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    def locking(self, fileno: int, mode: int, nbytes: int) -> None:
        kind = 'LOCK' if mode == self.LK_LOCK else 'UNLCK'
        self.calls.append((kind, fileno, nbytes))


def _exercise_lock_under_msvcrt(module, lock_callable) -> _RecordingMsvcrt:
    """Run ``_process_safe_write_lock`` with the msvcrt path forced."""
    recorder = _RecordingMsvcrt()
    with patch.object(module, 'fcntl', None), \
         patch.object(module, 'msvcrt', recorder):
        with tempfile.TemporaryDirectory() as td:
            sidecar = Path(td) / 'store.json'
            with lock_callable(sidecar):
                # While the lock is held, the sidecar's .lock file exists.
                assert (sidecar.with_suffix(sidecar.suffix + '.lock')).is_file()
    return recorder


class CrossPlatformLockWiringTests(unittest.TestCase):

    # ----- POSIX environment (this machine) -----

    def test_repository_approval_service_imports_with_fcntl_on_posix(self) -> None:
        # POSIX expectation: fcntl is the active lock module.
        self.assertIsNotNone(repository_approval_service.fcntl)

    def test_comment_store_imports_with_fcntl_on_posix(self) -> None:
        self.assertIsNotNone(comment_store.fcntl)

    # ----- forced no-platform degradation -----

    def test_lock_degrades_to_noop_when_neither_module_is_present(self) -> None:
        """If both fcntl AND msvcrt are unavailable, the helper yields without locking."""
        for module, helper_name in (
            (repository_approval_service, '_process_safe_write_lock'),
            (comment_store, '_process_safe_write_lock'),
        ):
            helper = getattr(module, helper_name)
            with patch.object(module, 'fcntl', None), \
                 patch.object(module, 'msvcrt', None):
                with tempfile.TemporaryDirectory() as td:
                    entered = False
                    with helper(Path(td) / 'x.json'):
                        entered = True
                    self.assertTrue(entered, f'{helper_name} did not yield')

    # ----- forced msvcrt path (Windows-shape, POSIX-runtime) -----

    def test_approval_lock_routes_through_msvcrt_locking_when_fcntl_absent(
        self,
    ) -> None:
        recorder = _exercise_lock_under_msvcrt(
            repository_approval_service,
            repository_approval_service._process_safe_write_lock,
        )
        # The lock helper must call ``locking(fileno, LK_LOCK, 1)`` on
        # acquire and ``locking(fileno, LK_UNLCK, 1)`` on release.
        kinds = [c[0] for c in recorder.calls]
        self.assertEqual(
            kinds, ['LOCK', 'UNLCK'],
            f'msvcrt lock sequence wrong: {recorder.calls}',
        )
        # Locks exactly 1 byte (the documented contract).
        for kind, fileno, nbytes in recorder.calls:
            self.assertEqual(nbytes, 1, 'msvcrt should lock 1 byte')
            self.assertIsInstance(fileno, int)

    def test_comment_store_lock_routes_through_msvcrt_locking_when_fcntl_absent(
        self,
    ) -> None:
        recorder = _exercise_lock_under_msvcrt(
            comment_store,
            comment_store._process_safe_write_lock,
        )
        kinds = [c[0] for c in recorder.calls]
        self.assertEqual(kinds, ['LOCK', 'UNLCK'])

    def test_msvcrt_lockfile_is_created_in_sidecar_parent(self) -> None:
        """The msvcrt branch opens a real lockfile next to the sidecar."""
        for module in (repository_approval_service, comment_store):
            helper = module._process_safe_write_lock
            recorder = _RecordingMsvcrt()
            with patch.object(module, 'fcntl', None), \
                 patch.object(module, 'msvcrt', recorder):
                with tempfile.TemporaryDirectory() as td:
                    sidecar = Path(td) / 'sub' / 'store.json'
                    with helper(sidecar):
                        # The lockfile must exist while we're inside.
                        lock_path = sidecar.with_suffix(
                            sidecar.suffix + '.lock',
                        )
                        self.assertTrue(
                            lock_path.is_file(),
                            f'{module.__name__}: lockfile not created at '
                            f'{lock_path}',
                        )

    def test_msvcrt_lock_retries_on_oserror_until_success(self) -> None:
        """``LK_LOCK`` blocks up to ~10s then raises; the helper retries.

        The approval-service helper has a ``while True: try ... except
        OSError: continue`` loop around ``msvcrt.locking``. Verify it
        actually retries by raising on the first call and succeeding
        on the second.
        """

        class _FlakeyMsvcrt(_RecordingMsvcrt):
            def __init__(self) -> None:
                super().__init__()
                self._call_count = 0

            def locking(self, fileno, mode, nbytes) -> None:
                self._call_count += 1
                if mode == self.LK_LOCK and self._call_count == 1:
                    raise OSError('would block')
                super().locking(fileno, mode, nbytes)

        recorder = _FlakeyMsvcrt()
        with patch.object(repository_approval_service, 'fcntl', None), \
             patch.object(repository_approval_service, 'msvcrt', recorder):
            with tempfile.TemporaryDirectory() as td:
                entered = False
                with repository_approval_service._process_safe_write_lock(
                    Path(td) / 'store.json',
                ):
                    entered = True
                self.assertTrue(entered, 'lock helper never yielded')
        # The first attempt raised (not recorded) → 1 successful LOCK
        # was recorded → 1 UNLCK. Total locking() invocations: 3
        # (failed LOCK + successful LOCK + UNLCK).
        kinds = [c[0] for c in recorder.calls]
        self.assertEqual(kinds, ['LOCK', 'UNLCK'])
        self.assertEqual(recorder._call_count, 3,
                         'helper must retry after a first OSError')


if __name__ == '__main__':
    unittest.main()
