"""Behavioural tests for ``kato.sandbox.manager._validate_workspace_path``.

The drift guard in ``test_bypass_protections_doc_consistency.py`` enforces
set-equality between the code constants and the documented anchor
blocks — but set-equality alone doesn't catch the case where someone
moves a path between the SUBTREE set and the EXACT set with semantic
consequences. The clearest example, and the bug this file exists to
prevent regressing:

    ``~/.kato`` belongs in the EXACT set, not the SUBTREE set.

If it ever moves back to SUBTREE, the validator will refuse every
default-configured per-task workspace (which lives at
``~/.kato/workspaces/<task_id>/<repo>/`` per
``workspace_manager.DEFAULT_ROOT_DIR_NAME = '.kato/workspaces'``) and
kato will be unable to spawn sandboxed Claude at all.

These tests exercise the validator against representative paths so
the drift cannot land silently.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from sandbox_core_lib.sandbox_core_lib import manager


class _WorkspacePathTests(unittest.TestCase):
    """End-to-end ``_validate_workspace_path`` behaviour.

    Every test creates real directories under a managed cleanup root
    so the validator's ``exists()`` / ``is_dir()`` checks pass on
    legitimate paths and fail honestly on synthetic ones.
    """

    def setUp(self) -> None:
        self._cleanup: list[Path] = []

    def tearDown(self) -> None:
        for path in self._cleanup:
            shutil.rmtree(path, ignore_errors=True)

    def _mkdir(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        # Track the topmost-created ancestor for cleanup. Walk up
        # until we find a parent that already existed when the test
        # started; the first newly-created ancestor is the cleanup
        # target.
        ancestor = path
        while ancestor.parent != ancestor and not any(
            ancestor.parent == c for c in self._cleanup
        ):
            ancestor = ancestor.parent
            if ancestor.exists() and ancestor not in self._cleanup:
                # Only register paths *we* created — never an existing
                # system directory like Path.home() itself.
                if str(ancestor).startswith(tempfile.gettempdir()) or \
                        str(ancestor).startswith(str(Path.home() / '.kato')):
                    self._cleanup.append(ancestor)
                    break
        return path

    # ----- the regression test that motivates this file -----

    def test_legitimate_default_workspace_under_dotkato_workspaces_is_accepted(self):
        """``~/.kato/workspaces/<task>/<repo>`` MUST validate.

        This is the default per-task workspace path
        (``workspace_manager.DEFAULT_ROOT_DIR_NAME = '.kato/workspaces'``).
        If ``~/.kato`` ever lands back in
        ``_FORBIDDEN_MOUNT_SOURCES_SUBTREE``, this test fails — and
        kato would otherwise refuse every default-configured spawn.
        """
        wpath = Path.home() / '.kato' / 'workspaces' / 'KATO-TEST-1' / 'somerepo'
        self._mkdir(wpath)
        out = manager._validate_workspace_path(str(wpath))
        self.assertEqual(Path(out), wpath.resolve())

    def test_dotkato_itself_is_refused(self):
        """``~/.kato`` exact-match is still refused (would expose audit log + sibling workspaces)."""
        with self.assertRaises(manager.SandboxError) as ctx:
            manager._validate_workspace_path(str(Path.home() / '.kato'))
        self.assertIn('system or home directory', str(ctx.exception))

    def test_dotkato_subtree_other_than_workspaces_is_allowed(self):
        """Descendants of ``~/.kato`` are allowed in general (operator can configure).

        ``KATO_WORKSPACES_ROOT`` may point anywhere; if an operator
        configures it as a non-default subdir of ``~/.kato``, that
        path must still validate. The exact-match rule rejects only
        ``~/.kato`` itself.
        """
        wpath = Path.home() / '.kato' / 'custom-workspaces-root' / 'task' / 'repo'
        self._mkdir(wpath)
        out = manager._validate_workspace_path(str(wpath))
        self.assertEqual(Path(out), wpath.resolve())

    # ----- representative subtree-forbidden checks -----

    def test_dotssh_subtree_is_refused(self):
        """``~/.ssh`` and any descendant must be refused (key theft risk)."""
        for path_str in (
            str(Path.home() / '.ssh'),
            str(Path.home() / '.ssh' / 'fake-subdir'),
        ):
            with self.subTest(path=path_str):
                with self.assertRaises(manager.SandboxError):
                    manager._validate_workspace_path(path_str)

    def test_etc_subtree_is_refused(self):
        """``/etc`` and any descendant must be refused."""
        for path_str in ('/etc', '/etc/passwd', '/etc/some/deep/path'):
            with self.subTest(path=path_str):
                with self.assertRaises(manager.SandboxError):
                    manager._validate_workspace_path(path_str)

    def test_docker_socket_paths_are_refused(self):
        """``/var/run/docker.sock`` and ``/var/lib/docker`` subtrees must be refused.

        Mounting either as a workspace would let Claude pivot to the
        host Docker daemon — the classic container-escape path.
        """
        for path_str in (
            '/var/run/docker.sock',
            '/var/lib/docker/something',
            '/run/docker.sock',
            '/run/containerd/anything',
        ):
            with self.subTest(path=path_str):
                with self.assertRaises(manager.SandboxError):
                    manager._validate_workspace_path(path_str)

    def test_macos_sensitive_library_subtrees_are_refused(self):
        """Every macOS-specific ``~/Library/...`` subtree in the forbidden
        set must reject the path itself AND a descendant.

        The validator checks set membership BEFORE existence, so this
        test runs identically on Linux / WSL2 / macOS — we are
        verifying the forbidden-subtree rule, not whether the path
        exists on this particular host. (On non-macOS hosts the
        paths simply don't exist; on macOS they hold Apple ID auth
        tokens, iMessage history, Mail data, Safari cookies / history,
        contacts, calendar, call history — every one of which a
        misconfigured workspace path could otherwise expose to the
        agent.)
        """
        macos_subtrees = [
            'Library/Cookies',
            'Library/Mail',
            'Library/Messages',
            'Library/Safari',
            'Library/Calendars',
            'Library/IdentityServices',
            'Library/Group Containers',
            'Library/Containers',
            'Library/Application Support/com.apple.sharedfilelist',
            'Library/Application Support/AddressBook',
            'Library/Application Support/Knowledge',
            'Library/Application Support/CallHistoryDB',
        ]
        for rel in macos_subtrees:
            base = Path.home() / rel
            with self.subTest(path=str(base), kind='self'):
                with self.assertRaises(manager.SandboxError) as ctx:
                    manager._validate_workspace_path(str(base))
                # Must be the subtree-forbidden rejection, not "does
                # not exist" — proves the entry is actually in the
                # forbidden set, not just absent on disk.
                msg = str(ctx.exception)
                self.assertTrue(
                    'sensitive directory' in msg or
                    'system or home directory' in msg,
                    msg=f'{base}: rejected for the wrong reason ({msg})',
                )
            descendant = base / 'synthetic-child-for-test'
            with self.subTest(path=str(descendant), kind='descendant'):
                with self.assertRaises(manager.SandboxError) as ctx:
                    manager._validate_workspace_path(str(descendant))
                self.assertIn(
                    'sensitive directory',
                    str(ctx.exception),
                    msg=f'{descendant}: descendant rejected for wrong reason',
                )

    # ----- exact-match-only checks (descendants are intentionally allowed) -----

    def test_home_exact_is_refused_but_subdir_is_allowed(self):
        """``$HOME`` exact-match is refused; ``$HOME/<subdir>`` is allowed.

        Per-task workspaces typically live under ``$HOME``, so subdirs
        must validate. ``$HOME`` itself is refused as too broad.
        """
        with self.assertRaises(manager.SandboxError):
            manager._validate_workspace_path(str(Path.home()))
        with tempfile.TemporaryDirectory(dir=Path.home()) as t:
            out = manager._validate_workspace_path(t)
            self.assertEqual(Path(out), Path(t).resolve())

    def test_root_dir_exact_is_refused(self):
        with self.assertRaises(manager.SandboxError):
            manager._validate_workspace_path('/')

    # ----- non-existent / non-directory checks -----

    def test_non_existent_path_is_refused(self):
        with self.assertRaises(manager.SandboxError) as ctx:
            manager._validate_workspace_path('/this/path/does/not/exist/anywhere')
        self.assertIn('does not exist', str(ctx.exception))

    def test_file_instead_of_dir_is_refused(self):
        # Create the file under $HOME so the path doesn't accidentally
        # land in a forbidden subtree (on macOS, the system temp dir
        # resolves under ``/private/var/folders/...`` which is
        # subtree-forbidden — the dir-vs-file check would never fire
        # because the subtree check runs first).
        with tempfile.NamedTemporaryFile(dir=Path.home()) as fp:
            with self.assertRaises(manager.SandboxError) as ctx:
                manager._validate_workspace_path(fp.name)
            self.assertIn('not a directory', str(ctx.exception))

    def test_empty_path_is_refused(self):
        for path_str in ('', '   '):
            with self.subTest(path=repr(path_str)):
                with self.assertRaises(manager.SandboxError) as ctx:
                    manager._validate_workspace_path(path_str)
                self.assertIn('empty', str(ctx.exception))

    # ----- workspace-internal docker socket scan -----

    def test_workspace_containing_docker_sock_is_refused(self):
        """A workspace whose top-level contains ``docker.sock`` must be refused.

        Docker-in-docker setups sometimes leave a ``docker.sock``
        symlink in the project root; mounting that workspace would
        give Claude a path to the host daemon.
        """
        with tempfile.TemporaryDirectory(dir=Path.home()) as t:
            (Path(t) / 'docker.sock').touch()
            with self.assertRaises(manager.SandboxError) as ctx:
                manager._validate_workspace_path(t)
            self.assertIn('Docker', str(ctx.exception))

    def test_workspace_containing_containerd_sock_is_refused(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as t:
            (Path(t) / 'containerd.sock').touch()
            with self.assertRaises(manager.SandboxError):
                manager._validate_workspace_path(t)

    def test_workspace_with_non_socket_entries_is_accepted(self):
        # Branch 967->966: ``iterdir`` yields entries whose name does NOT
        # match docker.sock / containerd.sock — the loop must skip them
        # and keep walking. Ensures the socket-scan doesn't false-positive
        # on regular project files.
        with tempfile.TemporaryDirectory(dir=Path.home()) as t:
            (Path(t) / 'README.md').write_text('hi')
            (Path(t) / 'src').mkdir()
            result = manager._validate_workspace_path(t)
            self.assertEqual(result, str(Path(t).resolve()))


if __name__ == '__main__':
    unittest.main()
