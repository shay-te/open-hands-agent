"""Coverage for ``JiraCoreLib`` constructor."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jira_core_lib.jira_core_lib.jira_core_lib import JiraCoreLib


class JiraCoreLibInitTests(unittest.TestCase):
    def test_composes_issue_client(self) -> None:
        jira_cfg = SimpleNamespace(
            base_url='https://example.atlassian.net',
            token='token',
            email='me@example.com',
            max_retries=3,
        )
        cfg = SimpleNamespace(core_lib=SimpleNamespace(
            jira_core_lib=jira_cfg,
        ))
        with patch(
            'jira_core_lib.jira_core_lib.jira_core_lib.JiraClient',
        ) as client_cls:
            lib = JiraCoreLib(cfg)
        self.assertIsNotNone(lib.issue)
        # ``bot_login=''`` because the SimpleNamespace omits
        # ``assignee`` and the bootstrap defaults missing values to
        # empty (filter disabled — preserves pre-change behavior).
        client_cls.assert_called_once_with(
            'https://example.atlassian.net', 'token',
            'me@example.com', 3,
            bot_login='',
        )

    def test_passes_assignee_through_as_bot_login(self) -> None:
        # When the config exposes ``assignee``, it's threaded through
        # to ``JiraClient.bot_login`` so the @-mention filter has the
        # bot's login to compare against.
        jira_cfg = SimpleNamespace(
            base_url='https://example.atlassian.net',
            token='token',
            email='me@example.com',
            max_retries=3,
            assignee='kato_bot',
        )
        cfg = SimpleNamespace(core_lib=SimpleNamespace(
            jira_core_lib=jira_cfg,
        ))
        with patch(
            'jira_core_lib.jira_core_lib.jira_core_lib.JiraClient',
        ) as client_cls:
            JiraCoreLib(cfg)
        client_cls.assert_called_once_with(
            'https://example.atlassian.net', 'token',
            'me@example.com', 3,
            bot_login='kato_bot',
        )


if __name__ == '__main__':
    unittest.main()
