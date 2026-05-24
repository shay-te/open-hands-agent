"""Tests for the non-overridable git denylist enforced on every Claude spawn.

Kato is the only component that runs git operations. Claude must never
invoke git directly, regardless of operator-supplied tool config or
permission mode.
"""

from __future__ import annotations

import unittest

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient


class GitDenylistMergeTests(unittest.TestCase):
    def test_empty_operator_disallowed_still_denies_git(self) -> None:
        merged = ClaudeCliClient._merge_disallowed_with_git_deny('')
        for pattern in ClaudeCliClient.GIT_DENY_PATTERNS:
            self.assertIn(pattern, merged.split(','))

    def test_operator_extension_is_preserved(self) -> None:
        merged = ClaudeCliClient._merge_disallowed_with_git_deny('Bash(rm:*),WebFetch')
        items = merged.split(',')
        self.assertIn('Bash(rm:*)', items)
        self.assertIn('WebFetch', items)
        for pattern in ClaudeCliClient.GIT_DENY_PATTERNS:
            self.assertIn(pattern, items)

    def test_git_patterns_are_not_duplicated(self) -> None:
        already = ClaudeCliClient.GIT_DENY_PATTERNS[0]
        merged = ClaudeCliClient._merge_disallowed_with_git_deny(already)
        items = merged.split(',')
        self.assertEqual(items.count(already), 1)

    def test_operator_cannot_remove_git_patterns_via_omission(self) -> None:
        merged = ClaudeCliClient._merge_disallowed_with_git_deny('OnlyMyTool')
        # Even though the operator's value didn't include git, the merge
        # adds the git patterns. There is no operator input shape that
        # produces a merged string lacking the git patterns.
        for pattern in ClaudeCliClient.GIT_DENY_PATTERNS:
            self.assertIn(pattern, merged)


class CommandIncludesGitDenyTests(unittest.TestCase):
    def _build(self, **kwargs) -> list[str]:
        client = ClaudeCliClient(binary='claude', **kwargs)
        return client._build_command(additional_dirs=[], agent_session_id='')

    def test_safe_mode_command_includes_git_deny(self) -> None:
        command = self._build(bypass_permissions=False)
        # --disallowedTools is always present now (was conditional before).
        self.assertIn('--disallowedTools', command)
        idx = command.index('--disallowedTools')
        flag_value = command[idx + 1]
        for pattern in ClaudeCliClient.GIT_DENY_PATTERNS:
            self.assertIn(pattern, flag_value)

    def test_bypass_mode_command_still_includes_git_deny(self) -> None:
        command = self._build(bypass_permissions=True)
        self.assertIn('--disallowedTools', command)
        idx = command.index('--disallowedTools')
        flag_value = command[idx + 1]
        for pattern in ClaudeCliClient.GIT_DENY_PATTERNS:
            self.assertIn(
                pattern, flag_value,
                'git deny patterns must apply even when bypass_permissions=True',
            )

    def test_operator_disallowed_tools_combined_with_git_deny(self) -> None:
        command = self._build(disallowed_tools='WebFetch')
        idx = command.index('--disallowedTools')
        flag_value = command[idx + 1]
        self.assertIn('WebFetch', flag_value)
        for pattern in ClaudeCliClient.GIT_DENY_PATTERNS:
            self.assertIn(pattern, flag_value)


if __name__ == '__main__':
    unittest.main()
