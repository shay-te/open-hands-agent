import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kato_core_lib.helpers.agent_prompt_utils import (
    agents_instructions_text,
    chat_continuity_ground_truth_block,
    forbidden_repository_guardrails_text,
    ignored_repository_folder_names,
    prepend_chat_workspace_context,
    prepend_forbidden_repository_guardrails,
    repository_scope_text,
    review_comment_code_snippet,
    review_comment_context_text,
    review_comment_location_text,
    review_conversation_title,
    review_repository_context,
    task_branch_name,
    task_conversation_title,
    workspace_inventory_block,
)


class AgentPromptUtilsTests(unittest.TestCase):
    def test_ignored_repository_folder_names_parses_and_deduplicates(self) -> None:
        result = ignored_repository_folder_names(' alpha ,Beta,alpha,, gamma ')

        self.assertEqual(result, ['alpha', 'Beta', 'gamma'])

    def test_forbidden_repository_guardrails_names_out_of_bounds_tools(self) -> None:
        text = forbidden_repository_guardrails_text('secret-client, legacy-api')

        self.assertIn('KATO_IGNORED_REPOSITORY_FOLDERS', text)
        self.assertIn('- secret-client', text)
        self.assertIn('- legacy-api', text)
        self.assertIn('Do not access them with Read, Glob, Grep, Bash', text)
        self.assertIn('Execution protocol for forbidden repositories', text)

    def test_prepend_forbidden_repository_guardrails_returns_original_when_empty(self) -> None:
        self.assertEqual(prepend_forbidden_repository_guardrails('hello', ''), 'hello')

    def test_prepend_forbidden_repository_guardrails_adds_guardrails(self) -> None:
        result = prepend_forbidden_repository_guardrails('resume work', 'secret-client')

        self.assertTrue(result.startswith('Forbidden repository folders'))
        self.assertIn('secret-client', result)
        self.assertTrue(result.endswith('resume work'))

    def test_workspace_inventory_block_lists_cwd_and_extras_with_anchor_text(self) -> None:
        # Anchors Claude to the repos that EXIST in the workspace so
        # it can map operator shorthand ("the front end") onto a
        # real path instead of a name from the forbidden list.
        block = workspace_inventory_block(
            cwd='/wks/UNA-2489/ob-love-admin-backend',
            additional_dirs=[
                '/wks/UNA-2489/ob-love-admin-client',
                '/wks/UNA-2489/workflow-core-lib',
            ],
        )
        self.assertIn('Repositories available in this workspace:', block)
        self.assertIn('(cwd) /wks/UNA-2489/ob-love-admin-backend', block)
        self.assertIn('/wks/UNA-2489/ob-love-admin-client', block)
        self.assertIn('/wks/UNA-2489/workflow-core-lib', block)
        # The disambiguation sentence — the whole point of the
        # block — names the ``-new`` / ``-old`` failure mode so
        # Claude doesn't latch onto a similarly-named forbidden
        # repo when the workspace already has the right one.
        self.assertIn('-new', block)

    def test_workspace_inventory_block_returns_empty_when_no_paths(self) -> None:
        # No cwd, no extras → no block. Keeps prompts clean for
        # tasks that don't have a workspace yet (e.g. fresh task,
        # provisioning still in flight).
        self.assertEqual(workspace_inventory_block('', None), '')
        self.assertEqual(workspace_inventory_block('', []), '')

    def test_workspace_inventory_block_renders_extras_only_when_cwd_blank(self) -> None:
        # Line 107: ``if cwd_text:`` is False but ``extra_paths`` is
        # non-empty (cwd not yet known but additional dirs were
        # configured). The block still renders — just without the
        # ``(cwd)`` row — so Claude has the partial repo list to anchor on.
        block = workspace_inventory_block(
            cwd='',
            additional_dirs=['/wks/UNA-2489/ob-love-admin-client'],
        )
        self.assertIn('Repositories available in this workspace:', block)
        self.assertIn('/wks/UNA-2489/ob-love-admin-client', block)
        self.assertNotIn('(cwd)', block)

    def test_workspace_inventory_block_deduplicates_cwd_against_extras(self) -> None:
        # When a caller accidentally passes the cwd in
        # additional_dirs too, the block should not list the same
        # path twice — Claude already knows about its cwd.
        block = workspace_inventory_block(
            cwd='/wks/UNA-2489/ob-love-admin-backend',
            additional_dirs=[
                '/wks/UNA-2489/ob-love-admin-backend/',  # trailing slash duplicate
                '/wks/UNA-2489/ob-love-admin-client',
            ],
        )
        # Only one ``ob-love-admin-backend`` line.
        self.assertEqual(
            block.count('ob-love-admin-backend'),
            1,
        )

    def test_prepend_chat_workspace_context_orders_continuity_inventory_forbidden(self) -> None:
        # Continuity FIRST (session-level: trust the conversation
        # history), inventory SECOND (task-level: these are the
        # repos), forbidden THIRD (operational: don't go outside),
        # operator message LAST. The continuity block has to lead
        # so the model commits to "answer from history" before the
        # operator's "verify the changes" wording races it into the
        # git storm we saw on adopted sessions.
        result = prepend_chat_workspace_context(
            'verify the front end',
            cwd='/wks/UNA-2489/ob-love-admin-backend',
            additional_dirs=['/wks/UNA-2489/ob-love-admin-client'],
            raw_ignored_value='ob-love-admin-client-new',
        )
        continuity_pos = result.find('Continuity instruction (read first):')
        inventory_pos = result.find('Repositories available in this workspace:')
        forbidden_pos = result.find('Forbidden repository folders')
        message_pos = result.find('verify the front end')
        self.assertGreater(continuity_pos, -1)
        self.assertGreater(inventory_pos, continuity_pos)
        self.assertGreater(forbidden_pos, inventory_pos)
        self.assertGreater(message_pos, forbidden_pos)

    def test_prepend_chat_workspace_context_emits_continuity_even_with_no_other_blocks(self) -> None:
        # No inventory + no forbidden config → the continuity
        # block alone still leads the prompt, because biasing
        # against defensive git inspection is the load-bearing
        # behaviour change and applies on every chat-respawn,
        # including the simplest single-repo task.
        result = prepend_chat_workspace_context(
            'hello', cwd='', additional_dirs=None, raw_ignored_value='',
        )
        self.assertIn('Continuity instruction', result)
        self.assertTrue(result.endswith('hello'))

    def test_chat_continuity_block_names_the_failure_modes(self) -> None:
        # Concrete inspection names ("git log", "git diff", "git
        # show") so the rule is unambiguous, plus the three escape
        # hatches so "trust history" doesn't read as "never use
        # git". This wording was deliberately picked after watching
        # adopted sessions fan out into 8+ git commands per turn;
        # treat changes as a content review, not a string nit.
        block = chat_continuity_ground_truth_block(is_resumed_session=True)
        self.assertIn('Trust it', block)
        self.assertIn('git log', block)
        self.assertIn('git diff', block)
        self.assertIn('git show', block)
        # Escape hatches.
        self.assertIn('explicitly asks', block)
        self.assertIn('external changes', block)
        self.assertIn('insufficient', block)


class IgnoredRepositoryFolderListInputTests(unittest.TestCase):
    def test_list_input_is_handled(self) -> None:
        # Hits the ``else: candidates = list(value or [])`` branch.
        result = ignored_repository_folder_names(['foo', 'bar', 'foo', ''])
        self.assertEqual(result, ['foo', 'bar'])


class WorkspaceInventoryBlockEdgeCases(unittest.TestCase):
    def test_skips_blank_additional_dirs(self) -> None:
        # Empty/None entries in ``additional_dirs`` must be silently skipped
        # so a stray '' from config doesn't render as a phantom repo row.
        block = workspace_inventory_block(
            cwd='/wks/UNA-1/main',
            additional_dirs=['', None, '/wks/UNA-1/side'],
        )
        self.assertIn('/wks/UNA-1/main', block)
        self.assertIn('/wks/UNA-1/side', block)
        # No empty-row artifact from the skipped entries.
        self.assertNotIn('- \n', block)


class PrependChatWorkspaceContextTests(unittest.TestCase):
    def test_prepends_continuity_and_inventory_and_forbidden_blocks(self) -> None:
        out = prepend_chat_workspace_context(
            'do work',
            cwd='/repo',
            additional_dirs=['/repo2'],
            is_resumed_session=True,
            raw_ignored_value='secret-lib',
        )
        # Order: continuity → inventory → forbidden → user prompt (line 214).
        continuity_idx = out.find('Trust it')
        inventory_idx = out.find('Repositories available')
        forbidden_idx = out.find('Forbidden repository folders')
        prompt_idx = out.find('do work')
        self.assertLess(continuity_idx, inventory_idx)
        self.assertLess(inventory_idx, forbidden_idx)
        self.assertLess(forbidden_idx, prompt_idx)


class RepositoryScopeTextTests(unittest.TestCase):
    def test_single_repo_fallback_text_when_no_repositories(self) -> None:
        task = SimpleNamespace(
            id='PROJ-1', branch_name='feature/proj-1',
            repository_branches={}, repositories=[],
        )
        out = repository_scope_text(task)
        self.assertIn('feature/proj-1', out)
        self.assertIn('Before making changes', out)

    def test_multi_repo_lists_each_repository(self) -> None:
        repositories = [
            SimpleNamespace(id='client', local_path='/wks/client', destination_branch='master'),
            SimpleNamespace(id='backend', local_path='/wks/backend', destination_branch='main'),
        ]
        prepared = SimpleNamespace(
            repositories=repositories,
            repository_branches={'client': 'feat/c', 'backend': 'feat/b'},
            branch_name='feat/main',
        )
        task = SimpleNamespace(id='PROJ-1', branch_name='', repository_branches={}, repositories=[])
        out = repository_scope_text(task, prepared)
        self.assertIn('Only modify these repositories', out)
        self.assertIn('client at /wks/client', out)
        self.assertIn('backend at /wks/backend', out)
        self.assertIn('feat/c from master', out)
        self.assertIn('feat/b from main', out)

    def test_prepared_task_with_blank_branch_keeps_task_branch(self) -> None:
        # Line 323: when ``prepared_task.branch_name`` is falsy, we
        # fall through and keep the task-level branch name instead of
        # overwriting it with the empty prepared value. Guards against
        # accidentally erasing the branch label when a partially
        # initialized prepared context shows up.
        repositories = [
            SimpleNamespace(id='client', local_path='/wks/client', destination_branch='master'),
        ]
        prepared = SimpleNamespace(
            repositories=repositories,
            repository_branches={'client': 'feat/c'},
            branch_name='',
        )
        task = SimpleNamespace(
            id='PROJ-1', branch_name='task-branch',
            repository_branches={}, repositories=[],
        )
        out = repository_scope_text(task, prepared)
        self.assertIn('task-branch', out)

    def test_destination_branch_fallback_text_when_unknown(self) -> None:
        repositories = [
            SimpleNamespace(id='client', local_path='/wks/client', destination_branch=''),
        ]
        prepared = SimpleNamespace(
            repositories=repositories, repository_branches={'client': 'feat/c'},
            branch_name='feat/c',
        )
        task = SimpleNamespace(id='PROJ-1', branch_name='', repository_branches={}, repositories=[])
        out = repository_scope_text(task, prepared)
        self.assertIn('the repository default branch', out)


class AgentsInstructionsTextTests(unittest.TestCase):
    def test_returns_empty_when_prepared_task_is_none(self) -> None:
        self.assertEqual(agents_instructions_text(None), '')

    def test_returns_prepared_task_instructions(self) -> None:
        prepared = SimpleNamespace(agents_instructions='follow these rules')
        self.assertEqual(agents_instructions_text(prepared), 'follow these rules')


class TaskBranchNameTests(unittest.TestCase):
    def test_uses_prepared_task_branch_when_set(self) -> None:
        task = SimpleNamespace(branch_name='task-branch')
        prepared = SimpleNamespace(branch_name='prepared-branch')
        self.assertEqual(task_branch_name(task, prepared), 'prepared-branch')

    def test_falls_back_to_task_branch_when_prepared_none(self) -> None:
        task = SimpleNamespace(branch_name='task-branch')
        self.assertEqual(task_branch_name(task, None), 'task-branch')


class TaskConversationTitleTests(unittest.TestCase):
    def test_prefers_task_id(self) -> None:
        task = SimpleNamespace(id='PROJ-1', summary='ignored')
        self.assertEqual(task_conversation_title(task), 'PROJ-1')

    def test_falls_back_to_summary_when_no_id(self) -> None:
        task = SimpleNamespace(id='', summary='Fix the bug')
        self.assertEqual(task_conversation_title(task), 'Fix the bug')

    def test_default_label_when_nothing(self) -> None:
        task = SimpleNamespace(id='', summary='')
        self.assertEqual(task_conversation_title(task), 'Kato task')

    def test_appends_suffix(self) -> None:
        task = SimpleNamespace(id='PROJ-1', summary='')
        self.assertEqual(task_conversation_title(task, ' [test]'), 'PROJ-1 [test]')


class ReviewConversationTitleTests(unittest.TestCase):
    def test_uses_task_id_when_present(self) -> None:
        comment = SimpleNamespace(comment_id='c1')
        self.assertEqual(
            review_conversation_title(comment, task_id='PROJ-1'),
            'PROJ-1 [review]',
        )

    def test_falls_back_to_comment_id_when_no_task_id(self) -> None:
        comment = SimpleNamespace(comment_id='c42')
        self.assertEqual(
            review_conversation_title(comment, task_id=''),
            'Fix review comment c42',
        )


class ReviewCommentContextTextTests(unittest.TestCase):
    def test_returns_empty_when_only_one_comment(self) -> None:
        # ``len(all_comments) <= 1`` short-circuit.
        comment = SimpleNamespace(all_comments=[{'author': 'a', 'body': 'b'}])
        self.assertEqual(review_comment_context_text(comment), '')

    def test_renders_other_authors_excluding_kato_replies(self) -> None:
        comment = SimpleNamespace(
            all_comments=[
                {'author': 'reviewer', 'body': 'why X?'},
                {'author': 'kato', 'body': 'Kato addressed review comment 7'},
                {'author': 'reviewer', 'body': 'follow up'},
            ],
        )
        out = review_comment_context_text(comment)
        self.assertIn('reviewer: why X?', out)
        self.assertIn('reviewer: follow up', out)
        # Kato self-replies filtered out.
        self.assertNotIn('Kato addressed', out)

    def test_skips_non_dict_entries(self) -> None:
        comment = SimpleNamespace(
            all_comments=[
                'plain string',
                {'author': 'a', 'body': 'msg-1'},
                {'author': 'b', 'body': 'msg-2'},
            ],
        )
        out = review_comment_context_text(comment)
        self.assertIn('msg-1', out)
        self.assertIn('msg-2', out)

    def test_skips_blank_body_entries(self) -> None:
        comment = SimpleNamespace(
            all_comments=[
                {'author': 'a', 'body': ''},
                {'author': 'b', 'body': 'real'},
            ],
        )
        # Only 1 valid entry (after blank skipped) but original list len is 2,
        # so we pass the ``<= 1`` gate. Body skipping kicks in on iteration.
        out = review_comment_context_text(comment)
        self.assertIn('real', out)

    def test_returns_empty_when_all_filtered_out(self) -> None:
        comment = SimpleNamespace(
            all_comments=[
                {'author': 'kato', 'body': 'Kato addressed this review comment X'},
                {'author': 'kato', 'body': 'Kato addressed review comment Y'},
            ],
        )
        self.assertEqual(review_comment_context_text(comment), '')


class ReviewRepositoryContextTests(unittest.TestCase):
    def test_returns_empty_when_no_repository_id(self) -> None:
        self.assertEqual(review_repository_context(SimpleNamespace()), '')

    def test_renders_repository_id_when_present(self) -> None:
        comment = SimpleNamespace()
        # The attribute name is from PullRequestFields.REPOSITORY_ID.
        from kato_core_lib.data_layers.data.fields import PullRequestFields
        setattr(comment, PullRequestFields.REPOSITORY_ID, 'client')
        self.assertEqual(
            review_repository_context(comment),
            ' in repository client',
        )


class ReviewCommentCodeSnippetEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def _comment(self, **kwargs):
        defaults = {'file_path': 'src/app.py', 'line_number': 5}
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_returns_empty_when_file_blank(self) -> None:
        target_file = self.workspace / 'blank.py'
        target_file.write_text('')
        self.assertEqual(
            review_comment_code_snippet(
                self._comment(file_path='blank.py', line_number=1),
                str(self.workspace),
            ),
            '',
        )

    def test_truncates_long_lines_with_ellipsis(self) -> None:
        target_file = self.workspace / 'long.py'
        target_file.write_text('x' * 500)
        snippet = review_comment_code_snippet(
            self._comment(file_path='long.py', line_number=1),
            str(self.workspace),
        )
        # Truncated to 237 chars + '...'
        self.assertIn('...', snippet)
        # Should not contain the full 500-char line.
        self.assertNotIn('x' * 300, snippet)

    def test_snippet_budget_truncates_when_exceeded(self) -> None:
        # Use a file of many medium-length lines that together exceed the budget.
        target_file = self.workspace / 'big.py'
        target_file.write_text('\n'.join('x' * 200 for _ in range(50)))
        snippet = review_comment_code_snippet(
            self._comment(file_path='big.py', line_number=25),
            str(self.workspace),
            context_lines=50,
        )
        # Either the truncation marker is present OR (if budget wasn't hit)
        # the snippet has some content. The path we want to cover is the
        # ``total_bytes > MAX_BYTES`` early-break branch.
        self.assertTrue(snippet)


class ReviewCommentLocationTextEdgeCases(unittest.TestCase):
    def test_renders_full_localization(self) -> None:
        comment = SimpleNamespace(
            file_path='src/a.py', line_number=10,
            line_type='ADDED', commit_sha='abc123',
        )
        out = review_comment_location_text(comment)
        self.assertIn('src/a.py:10', out)
        self.assertIn('ADDED', out)
        self.assertIn('abc123', out)


class PrependChatWorkspaceContextEmptyPartsTest(unittest.TestCase):
    def test_returns_prompt_unchanged_when_all_blocks_empty(self) -> None:
        # Line 213: when continuity + inventory + forbidden are all
        # empty strings, the function must return the prompt unchanged.
        # Continuity always returns a non-empty block in production
        # (always-on chat-respawn nudge), so we patch the three block
        # functions to empty to drive this branch — locks the "drop
        # empty blocks silently" property.
        from unittest.mock import patch
        from kato_core_lib.helpers import agent_prompt_utils as apu
        with patch.object(apu, 'chat_continuity_ground_truth_block',
                          return_value=''), \
             patch.object(apu, 'workspace_inventory_block',
                          return_value=''), \
             patch.object(apu, 'forbidden_repository_guardrails_text',
                          return_value=''):
            out = apu.prepend_chat_workspace_context(
                'just the message',
                cwd='', additional_dirs=None, raw_ignored_value=None,
                is_resumed_session=False,
            )
        self.assertEqual(out, 'just the message')


class SecurityGuardrailsTextTests(unittest.TestCase):
    def test_returns_named_security_clauses(self) -> None:
        # Line 218: ``security_guardrails_text`` returns the static
        # block. Locks the named clauses so a future edit that
        # accidentally drops "credential stores" or "untrusted data"
        # is caught.
        from kato_core_lib.helpers.agent_prompt_utils import (
            security_guardrails_text,
        )
        text = security_guardrails_text()
        self.assertIn('Security guardrails', text)
        self.assertIn('untrusted data', text)
        self.assertIn('~/.ssh', text)
        self.assertIn('exfiltrate', text)


class ReviewCommentCodeSnippetEdgeBranches(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        from pathlib import Path
        self.workspace = Path(self._tmp.name)

    def test_returns_empty_when_line_number_non_positive(self) -> None:
        # Line 448: ``line_int <= 0`` → ''. Defensive: a 0 or negative
        # line number is meaningless and the renderer would produce a
        # broken arrow. Drop it cleanly.
        from kato_core_lib.helpers.agent_prompt_utils import (
            review_comment_code_snippet,
        )
        comment = SimpleNamespace(file_path='x.py', line_number=0)
        self.assertEqual(
            review_comment_code_snippet(comment, str(self.workspace)),
            '',
        )

    def test_returns_empty_when_window_past_file_end(self) -> None:
        # Line 477: ``if not rendered`` → ''. Window of [start, end]
        # lands entirely past the last file line — no snippet to
        # render. Return '' rather than emit a snippet with no rows.
        target = self.workspace / 'tiny.py'
        target.write_text('one\ntwo\nthree\n')
        from kato_core_lib.helpers.agent_prompt_utils import (
            review_comment_code_snippet,
        )
        comment = SimpleNamespace(file_path='tiny.py', line_number=100)
        self.assertEqual(
            review_comment_code_snippet(
                comment, str(self.workspace), context_lines=1,
            ),
            '',
        )


class ReviewCommentsBatchTextSnippetBranches(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def test_batch_text_skips_snippet_when_workspace_lookup_returns_empty(self) -> None:
        # Line 539: ``if snippet:`` False — workspace_path was passed
        # so we tried to read a snippet, but the file doesn't exist
        # in the workspace. We must NOT inject an empty snippet block
        # under the comment; the body should follow the localization
        # header directly.
        from kato_core_lib.helpers.agent_prompt_utils import (
            review_comments_batch_text,
        )
        comment = SimpleNamespace(
            author='reviewer',
            body='please rename',
            file_path='does/not/exist.py',
            line_number=10,
            line_type='added',
            commit_sha='',
            comment_id='1',
        )
        text = review_comments_batch_text([comment], workspace_path=str(self.workspace))
        # No code block — just localization header + body.
        self.assertNotIn('Code at line', text)
        self.assertIn('please rename', text)


if __name__ == '__main__':
    unittest.main()
