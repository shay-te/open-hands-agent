"""Coverage tests for ``agent_prompt_utils`` not already exercised by
``test_agent_prompt_utils.py``.

The sibling file already covers the workspace_scope_block path-filter
branches, repository_scope_text's no-branch-override branch,
workspace_inventory_block's no-cwd branch, and the ignored-folders env
tests. This file fills in the REST: list-input dedupe, the guardrail
text bodies, inventory dedupe/skip branches, continuity block, the
prepend helpers, security guardrails, the full repository/agents/title
scope helpers, and the review-comment context/snippet/batch/location
renderers including byte-budget truncation.

All fixtures are fake (``PROJ-1`` ids, tempfile paths, ``reviewer``
authors). No kato import, no network, no DB.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    IGNORED_REPOSITORY_FOLDERS_ENV,
    _is_self_reply_body,
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
    review_comments_batch_text,
    review_conversation_title,
    review_repository_context,
    security_guardrails_text,
    task_branch_name,
    task_conversation_title,
    workspace_inventory_block,
    workspace_scope_block,
)


class IgnoredRepositoryFolderNamesListTests(unittest.TestCase):
    """Lines 38, 45: the list-input branch with None/dupes/case dedupe."""

    def test_list_input_dedupes_case_insensitively_and_drops_blanks(self) -> None:
        # raw_value is a list (not a string) → line 38 ``list(value or [])``.
        # 'Repo' and 'repo' collapse to one (key lowercased, line 45
        # ``continue`` on the duplicate); None and '' are dropped.
        names = ignored_repository_folder_names(
            ['Repo', None, 'repo', '  spaced  ', '', 'spaced'],
        )
        self.assertEqual(names, ['Repo', 'spaced'])

    def test_empty_list_returns_empty(self) -> None:
        self.assertEqual(ignored_repository_folder_names([]), [])

    def test_none_inside_list_or_value_falsy_yields_empty(self) -> None:
        # ``list(value or [])`` with a falsy non-None, non-str value
        # (e.g. an empty tuple) walks the same list branch and yields [].
        self.assertEqual(ignored_repository_folder_names(()), [])


class ForbiddenRepositoryGuardrailsTextTests(unittest.TestCase):
    def test_empty_names_returns_empty_string(self) -> None:
        # Line 54: no names → ''.
        self.assertEqual(forbidden_repository_guardrails_text([]), '')

    def test_non_empty_renders_header_and_bullets(self) -> None:
        text = forbidden_repository_guardrails_text(['secret-api', 'legacy-db'])
        self.assertIn(IGNORED_REPOSITORY_FOLDERS_ENV, text)
        self.assertIn('- secret-api', text)
        self.assertIn('- legacy-db', text)
        self.assertIn('out of bounds', text)


class WorkspaceInventoryBlockTests(unittest.TestCase):
    def test_cwd_dedupe_skip_blank_and_trailing_guidance(self) -> None:
        # Line 79: cwd added to seen (rstrip trailing slash). Line 82-83:
        # blank/None additional dirs skipped. Line 85-86: an additional
        # dir equal to cwd (modulo trailing slash) is deduped. Line 92-93:
        # the '(cwd)' line is emitted.
        block = workspace_inventory_block(
            cwd='/wks/PROJ/repo-a/',
            additional_dirs=[
                '',
                None,
                '/wks/PROJ/repo-a',   # same as cwd → deduped
                '/wks/PROJ/repo-b',   # genuinely extra
            ],
        )
        self.assertIn('- (cwd) /wks/PROJ/repo-a/', block)
        self.assertIn('- /wks/PROJ/repo-b', block)
        # The cwd path must appear only via the (cwd) line, never again
        # as a plain bullet (proves the dedupe at line 85-86 fired).
        self.assertNotIn('- /wks/PROJ/repo-a\n', block)
        self.assertIn('These are the ONLY repositories present', block)

    def test_extras_only_no_cwd_renders_without_cwd_line(self) -> None:
        # Branch 92->94: cwd_text is falsy at line 92 but extra_paths is
        # non-empty, so we fall through to render the extras with NO '(cwd)'
        # line. (The complementary path to test_cwd_dedupe above.)
        block = workspace_inventory_block(
            cwd='',
            additional_dirs=['/wks/PROJ/repo-only'],
        )
        self.assertIn('Repositories available in this workspace:', block)
        self.assertIn('- /wks/PROJ/repo-only', block)
        # No cwd → no '(cwd)' bullet at all.
        self.assertNotIn('(cwd)', block)

    def test_no_cwd_no_extras_returns_empty(self) -> None:
        # Line 89-90: nothing to render → ''.
        self.assertEqual(workspace_inventory_block('', None), '')
        self.assertEqual(workspace_inventory_block('   ', ['', None]), '')


class ChatContinuityGroundTruthBlockTests(unittest.TestCase):
    def test_returns_block_regardless_of_resumed_flag(self) -> None:
        # Line 111: the block text is identical for both flag values.
        resumed = chat_continuity_ground_truth_block(is_resumed_session=True)
        fresh = chat_continuity_ground_truth_block(is_resumed_session=False)
        self.assertEqual(resumed, fresh)
        self.assertIn('Continuity instruction', resumed)
        self.assertIn('authoritative record', resumed)


class PrependChatWorkspaceContextTests(unittest.TestCase):
    def test_all_blocks_joined_ahead_of_prompt(self) -> None:
        # Lines 145-150, 153: every block present → joined with the prompt.
        prompt = 'ORIGINAL-PROMPT'
        out = prepend_chat_workspace_context(
            prompt,
            cwd='/wks/PROJ/repo-a',
            additional_dirs=['/wks/PROJ/repo-b'],
            raw_ignored_value=['forbidden-one'],
            is_resumed_session=True,
        )
        self.assertIn('Continuity instruction', out)
        self.assertIn('Repositories available in this workspace:', out)
        self.assertIn('forbidden-one', out)
        # The prompt stays at the very end after all the context blocks.
        self.assertTrue(out.endswith(prompt))
        self.assertGreater(out.index('Continuity instruction'), -1)
        self.assertLess(out.index('Continuity instruction'), out.index(prompt))

    def test_no_blocks_returns_prompt_unchanged(self) -> None:
        # Lines 151-152: continuity is always non-empty, so to exercise the
        # ``not parts`` branch we would need every block empty. The
        # continuity block is unconditional, so this branch is reached only
        # when continuity itself is empty — which it never is. We still
        # assert the inventory/forbidden empties don't add noise, and that
        # the prompt survives as the tail. See notes for the residual.
        prompt = 'ONLY-PROMPT'
        out = prepend_chat_workspace_context(
            prompt,
            cwd='',
            additional_dirs=None,
            raw_ignored_value='',
        )
        # Continuity still prepends; prompt remains the tail.
        self.assertTrue(out.endswith(prompt))
        self.assertIn('Continuity instruction', out)


class SecurityGuardrailsTextTests(unittest.TestCase):
    def test_returns_structural_guardrail_text(self) -> None:
        # Line 157.
        text = security_guardrails_text()
        self.assertIn('Security guardrails:', text)
        self.assertIn('untrusted data', text)


class WorkspaceScopeBlockExtraGuidanceTests(unittest.TestCase):
    def test_non_empty_extra_is_appended(self) -> None:
        # Line 222-223: a non-empty extra is appended verbatim after block.
        block = workspace_scope_block(
            ['/wks/PROJ/repo-a'],
            extra_refusal_guidance='ASK THE OPERATOR TO WIDEN SCOPE.',
        )
        self.assertIn('ASK THE OPERATOR TO WIDEN SCOPE.', block)
        self.assertTrue(block.endswith('ASK THE OPERATOR TO WIDEN SCOPE.\n'))

    def test_whitespace_only_extra_is_treated_as_empty(self) -> None:
        # Line 221-224: whitespace strips to '' → returns the bare block.
        base = workspace_scope_block(['/wks/PROJ/repo-a'])
        with_ws = workspace_scope_block(
            ['/wks/PROJ/repo-a'], extra_refusal_guidance='   \n  ',
        )
        self.assertEqual(base, with_ws)


class PrependForbiddenRepositoryGuardrailsTests(unittest.TestCase):
    def test_prefixes_when_forbidden_list_present(self) -> None:
        # Lines 233, 236.
        out = prepend_forbidden_repository_guardrails(
            'BODY-PROMPT', ['secret-api'],
        )
        self.assertIn('secret-api', out)
        self.assertTrue(out.endswith('BODY-PROMPT'))
        self.assertLess(out.index('secret-api'), out.index('BODY-PROMPT'))

    def test_returns_prompt_unchanged_when_nothing_forbidden(self) -> None:
        # Lines 234-235.
        self.assertEqual(
            prepend_forbidden_repository_guardrails('BODY-PROMPT', []),
            'BODY-PROMPT',
        )


class RepositoryScopeTextTests(unittest.TestCase):
    def test_prepared_task_with_repositories_and_destination_branch(self) -> None:
        # Lines 251-254 region + 263-281: prepared_task drives the branch
        # name and repositories; repository_branches supplies a per-repo
        # branch; destination_branch present → named explicitly.
        repo = SimpleNamespace(
            id='repo-a',
            local_path='/wks/PROJ/repo-a',
            destination_branch='develop',
        )
        prepared = SimpleNamespace(
            repositories=[repo],
            repository_branches={'repo-a': 'feature/x'},
            branch_name='prepared-branch',
        )
        task = SimpleNamespace(branch_name='task-branch')
        out = repository_scope_text(task, prepared)
        self.assertIn('Only modify these repositories:', out)
        self.assertIn('repo-a at /wks/PROJ/repo-a', out)
        self.assertIn('prepared branch feature/x from develop', out)

    def test_prepared_task_fallback_branch_and_default_destination(self) -> None:
        # repository_branches absent → falls back to branches_by_repository.
        # A repo whose id is NOT in that map uses ``branch_name`` (line 266
        # ``.get`` default). destination_branch absent → 'the repository
        # default branch' (lines 269-271).
        repo = SimpleNamespace(
            id='repo-missing',
            local_path='/wks/PROJ/repo-missing',
            destination_branch='',
        )
        prepared = SimpleNamespace(
            repositories=[repo],
            repository_branches=None,
            branches_by_repository={'other-repo': 'unused'},
            branch_name='prepared-branch',
        )
        task = SimpleNamespace(branch_name='task-branch')
        out = repository_scope_text(task, prepared)
        # repo-missing not in the map → uses prepared branch_name fallback.
        self.assertIn('prepared branch prepared-branch from the repository default branch', out)

    def test_prepared_no_repos_empty_branch_falls_to_task_branch(self) -> None:
        # Branch 250->255 (prepared_task.branch_name is falsy → the
        # ``branch_name = prepared_task.branch_name`` assignment is SKIPPED)
        # plus line 255-262 (no repositories → the pull-and-branch fallback
        # text). With an empty prepared branch_name the task's branch_name
        # survives into the fallback message.
        prepared = SimpleNamespace(
            repositories=[],
            repository_branches={},
            branch_name='',
        )
        task = SimpleNamespace(branch_name='task-branch')
        out = repository_scope_text(task, prepared)
        self.assertNotIn('Only modify these repositories:', out)
        self.assertIn('create and work on a new branch named task-branch', out)

    def test_task_only_path_reads_task_fields(self) -> None:
        # Lines 252-254: prepared_task is None → repository_branches and
        # repositories come off ``task``.
        repo = SimpleNamespace(
            id='repo-z',
            local_path='/wks/PROJ/repo-z',
            destination_branch='main',
        )
        task = SimpleNamespace(
            branch_name='task-branch',
            repository_branches={'repo-z': 'topic'},
            repositories=[repo],
        )
        out = repository_scope_text(task, None)
        self.assertIn('repo-z at /wks/PROJ/repo-z', out)
        self.assertIn('prepared branch topic from main', out)


class AgentsInstructionsTextTests(unittest.TestCase):
    def test_none_prepared_returns_empty(self) -> None:
        # Line 285-286.
        self.assertEqual(agents_instructions_text(None), '')

    def test_returns_normalized_instructions(self) -> None:
        # Line 287: normalized (stripped).
        prepared = SimpleNamespace(agents_instructions='  follow the rules  ')
        self.assertEqual(agents_instructions_text(prepared), 'follow the rules')


class TaskBranchNameTests(unittest.TestCase):
    def test_prepared_branch_wins(self) -> None:
        # Lines 291-292.
        task = SimpleNamespace(branch_name='task-branch')
        prepared = SimpleNamespace(branch_name='prepared-branch')
        self.assertEqual(task_branch_name(task, prepared), 'prepared-branch')

    def test_falls_back_to_task_branch(self) -> None:
        # Line 293: prepared None or falsy branch_name → task's branch.
        task = SimpleNamespace(branch_name='  task-branch  ')
        self.assertEqual(task_branch_name(task, None), 'task-branch')
        prepared = SimpleNamespace(branch_name='')
        self.assertEqual(task_branch_name(task, prepared), 'task-branch')


class TaskConversationTitleTests(unittest.TestCase):
    def test_id_present(self) -> None:
        # Lines 297-299.
        task = SimpleNamespace(id='PROJ-1', summary='ignored')
        self.assertEqual(task_conversation_title(task), 'PROJ-1')

    def test_id_absent_summary_present(self) -> None:
        # Lines 300-302: no id → condensed summary.
        task = SimpleNamespace(id='', summary='  fix   the   bug  ')
        self.assertEqual(task_conversation_title(task), 'fix the bug')

    def test_both_absent_defaults_to_task(self) -> None:
        # Line 303.
        task = SimpleNamespace(id='', summary='')
        self.assertEqual(task_conversation_title(task), 'task')

    def test_suffix_is_appended(self) -> None:
        task = SimpleNamespace(id='PROJ-1', summary='')
        self.assertEqual(task_conversation_title(task, ' [chat]'), 'PROJ-1 [chat]')


class ReviewConversationTitleTests(unittest.TestCase):
    def test_task_id_present(self) -> None:
        # Lines 311-313.
        comment = SimpleNamespace(comment_id='c-9')
        self.assertEqual(
            review_conversation_title(comment, task_id='  PROJ-1  '),
            'PROJ-1 [review]',
        )

    def test_task_id_absent_uses_comment_id(self) -> None:
        # Line 314.
        comment = SimpleNamespace(comment_id='c-9')
        self.assertEqual(
            review_conversation_title(comment, task_id=''),
            'Fix review comment c-9',
        )


class ReviewCommentContextTextTests(unittest.TestCase):
    def test_non_list_returns_empty(self) -> None:
        # Line 319-320: all_comments not a list.
        comment = SimpleNamespace(all_comments='not-a-list')
        self.assertEqual(review_comment_context_text(comment), '')

    def test_single_comment_returns_empty(self) -> None:
        # Line 319-320: len <= 1.
        comment = SimpleNamespace(all_comments=[{'author': 'a', 'body': 'hi'}])
        self.assertEqual(review_comment_context_text(comment), '')

    def test_multiple_comments_render_author_and_body(self) -> None:
        # Lines 321-335: author present, author missing → 'reviewer'.
        comment = SimpleNamespace(
            all_comments=[
                {'author': 'alice', 'body': 'first point'},
                {'body': 'no author here'},   # author missing → 'reviewer'
            ],
        )
        out = review_comment_context_text(comment)
        self.assertIn('Review comment context:', out)
        self.assertIn('- alice: first point', out)
        self.assertIn('- reviewer: no author here', out)

    def test_self_reply_and_invalid_items_are_dropped(self) -> None:
        # Lines 323-330: non-dict skipped, empty body skipped, self-reply
        # body dropped. The self-reply prefix here is the function's
        # documented INPUT filter, not a test coupling.
        comment = SimpleNamespace(
            all_comments=[
                'not-a-dict',                                  # line 323-324 skip
                {'author': 'bob', 'body': ''},                # line 327-328 skip
                {'author': 'bot', 'body': 'Kato addressed review comment 5'},
                {'author': 'carol', 'body': 'keep me'},
            ],
        )
        out = review_comment_context_text(comment)
        self.assertIn('- carol: keep me', out)
        self.assertNotIn('Kato addressed', out)
        self.assertNotIn('bob', out)

    def test_everything_filtered_returns_empty(self) -> None:
        # Lines 333-334: list long enough but all entries filtered → ''.
        comment = SimpleNamespace(
            all_comments=[
                {'author': 'bot', 'body': 'Kato addressed this review comment'},
                {'author': 'bot2', 'body': ''},
            ],
        )
        self.assertEqual(review_comment_context_text(comment), '')


class IsSelfReplyBodyTests(unittest.TestCase):
    def test_matches_known_prefixes(self) -> None:
        # Line 352: both prefixes recognized; unrelated body is not.
        self.assertTrue(_is_self_reply_body('Kato addressed review comment 7'))
        self.assertTrue(_is_self_reply_body('Kato addressed this review comment now'))
        self.assertFalse(_is_self_reply_body('A normal review comment'))


class ReviewRepositoryContextTests(unittest.TestCase):
    def test_repository_id_present(self) -> None:
        # Lines 356-357.
        comment = SimpleNamespace(repository_id='repo-a')
        self.assertEqual(review_repository_context(comment), ' in repository repo-a')

    def test_repository_id_absent(self) -> None:
        comment = SimpleNamespace(repository_id='')
        self.assertEqual(review_repository_context(comment), '')


class ReviewCommentCodeSnippetTests(unittest.TestCase):
    def _write(self, tmp: str, name: str, text: str) -> str:
        path = Path(tmp) / name
        path.write_text(text, encoding='utf-8')
        return name

    def test_snippet_marks_target_line_with_context(self) -> None:
        # Lines 370-408: happy path with marker + context lines.
        with tempfile.TemporaryDirectory() as tmp:
            rel = self._write(
                tmp, 'mod.py',
                'line1\nline2\nline3\nline4\nline5\nline6\n',
            )
            comment = SimpleNamespace(file_path=rel, line_number=3)
            out = review_comment_code_snippet(comment, tmp)
            self.assertIn('Code at line 3:', out)
            self.assertIn('→', out)
            # Target line 3 is marked; context lines (e.g. line2/line4) show.
            self.assertIn('line3', out)
            self.assertIn('line2', out)
            self.assertIn('line4', out)

    def test_missing_file_path_returns_empty(self) -> None:
        # Lines 373-374.
        with tempfile.TemporaryDirectory() as tmp:
            comment = SimpleNamespace(file_path='', line_number=3)
            self.assertEqual(review_comment_code_snippet(comment, tmp), '')

    def test_missing_workspace_returns_empty(self) -> None:
        # Lines 373-374.
        comment = SimpleNamespace(file_path='mod.py', line_number=3)
        self.assertEqual(review_comment_code_snippet(comment, ''), '')

    def test_non_int_line_returns_empty(self) -> None:
        # Lines 376-378.
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'mod.py', 'a\nb\n')
            comment = SimpleNamespace(file_path='mod.py', line_number='not-a-number')
            self.assertEqual(review_comment_code_snippet(comment, tmp), '')

    def test_non_positive_line_returns_empty(self) -> None:
        # Lines 379-380.
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'mod.py', 'a\nb\n')
            comment = SimpleNamespace(file_path='mod.py', line_number=0)
            self.assertEqual(review_comment_code_snippet(comment, tmp), '')

    def test_missing_file_on_disk_returns_empty(self) -> None:
        # Lines 385-386: OSError on open → ''.
        with tempfile.TemporaryDirectory() as tmp:
            comment = SimpleNamespace(file_path='does-not-exist.py', line_number=1)
            self.assertEqual(review_comment_code_snippet(comment, tmp), '')

    def test_empty_file_returns_empty(self) -> None:
        # Lines 388-389: no lines after splitlines → ''.
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'empty.py', '')
            comment = SimpleNamespace(file_path='empty.py', line_number=1)
            self.assertEqual(review_comment_code_snippet(comment, tmp), '')

    def test_overlong_line_is_truncated(self) -> None:
        # Lines 397-398: a line longer than 240 chars → truncated with '...'.
        long_line = 'X' * 400
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'long.py', long_line + '\n')
            comment = SimpleNamespace(file_path='long.py', line_number=1)
            out = review_comment_code_snippet(comment, tmp)
            self.assertIn('...', out)
            # The original 400-char run must NOT survive verbatim.
            self.assertNotIn('X' * 400, out)

    def test_line_number_past_end_of_file_returns_empty(self) -> None:
        # Line 406-407: when line_number exceeds the file length, start
        # (max(1, line_int - ctx)) overshoots end (min(len(lines), ...)),
        # so range(start, end+1) is empty, the render loop never appends,
        # ``rendered`` stays empty → the ``if not rendered: return ''``
        # guard fires. (This branch IS reachable, unlike the residual claim.)
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'short.py', 'a\nb\n')
            comment = SimpleNamespace(file_path='short.py', line_number=100)
            self.assertEqual(review_comment_code_snippet(comment, tmp), '')

    def test_byte_budget_truncation(self) -> None:
        # Lines 402-404: total bytes exceed the 4096 budget → the
        # '... (snippet truncated)' marker is appended and the loop breaks.
        # Drive with many long lines and a wide context window so the
        # rendered block blows the budget.
        many_long = '\n'.join('Y' * 200 for _ in range(60)) + '\n'
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'big.py', many_long)
            comment = SimpleNamespace(file_path='big.py', line_number=30)
            out = review_comment_code_snippet(comment, tmp, context_lines=40)
            self.assertIn('... (snippet truncated)', out)


class ReviewCommentsBatchTextTests(unittest.TestCase):
    def test_empty_comments_returns_empty(self) -> None:
        # Lines 412-413.
        self.assertEqual(review_comments_batch_text([]), '')
        self.assertEqual(review_comments_batch_text(None), '')

    def test_pr_level_and_localized_headers(self) -> None:
        # Lines 414-434: a localized comment (file/line) and a PR-level
        # comment (no file/line) render distinct headers; author fallback.
        localized = SimpleNamespace(
            author='alice', body='change this',
            file_path='mod.py', line_number=4,
            line_type='', commit_sha='',
        )
        pr_level = SimpleNamespace(
            author='', body='overall looks ok',
            file_path='', line_number='',
            line_type='', commit_sha='',
        )
        out = review_comments_batch_text([localized, pr_level])
        self.assertIn('File: mod.py:4', out)
        self.assertIn('(no file/line — PR-level comment)', out)
        self.assertIn('Comment by alice: change this', out)
        # Missing author falls back to 'reviewer'.
        self.assertIn('Comment by reviewer: overall looks ok', out)
        self.assertTrue(out.endswith('\n'))

    def test_embeds_snippet_when_workspace_supplied(self) -> None:
        # Lines 425-431: workspace_path set + real file → snippet embedded.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'mod.py').write_text(
                'alpha\nbeta\ngamma\ndelta\n', encoding='utf-8',
            )
            comment = SimpleNamespace(
                author='bob', body='see here',
                file_path='mod.py', line_number=2,
                line_type='', commit_sha='',
            )
            out = review_comments_batch_text([comment], workspace_path=tmp)
            self.assertIn('Code at line 2:', out)
            self.assertIn('beta', out)

    def test_workspace_without_snippet_skips_embed(self) -> None:
        # Lines 425-427: workspace set but the comment yields no snippet
        # (PR-level) → no snippet block, just the header + body.
        with tempfile.TemporaryDirectory() as tmp:
            comment = SimpleNamespace(
                author='bob', body='general note',
                file_path='', line_number='',
                line_type='', commit_sha='',
            )
            out = review_comments_batch_text([comment], workspace_path=tmp)
            self.assertNotIn('Code at line', out)
            self.assertIn('Comment by bob: general note', out)


class ReviewCommentLocationTextTests(unittest.TestCase):
    def test_no_file_path_returns_empty(self) -> None:
        # Lines 442-443.
        comment = SimpleNamespace(
            file_path='', line_number=3, line_type='', commit_sha='',
        )
        self.assertEqual(review_comment_location_text(comment), '')

    def test_line_number_appended_when_positive_int(self) -> None:
        # Lines 444-448.
        comment = SimpleNamespace(
            file_path='mod.py', line_number=12, line_type='', commit_sha='',
        )
        self.assertEqual(review_comment_location_text(comment), 'File: mod.py:12')

    def test_invalid_line_number_omitted(self) -> None:
        # Lines 449-450: non-int line → no ':line' suffix.
        comment = SimpleNamespace(
            file_path='mod.py', line_number='nope', line_type='', commit_sha='',
        )
        self.assertEqual(review_comment_location_text(comment), 'File: mod.py')

    def test_non_positive_line_number_omitted(self) -> None:
        comment = SimpleNamespace(
            file_path='mod.py', line_number=0, line_type='', commit_sha='',
        )
        self.assertEqual(review_comment_location_text(comment), 'File: mod.py')

    def test_line_type_and_commit_sha_appended(self) -> None:
        # Lines 451-454: line_type in parens; commit on a new line.
        comment = SimpleNamespace(
            file_path='mod.py', line_number=7,
            line_type='ADDED', commit_sha='abc123',
        )
        out = review_comment_location_text(comment)
        self.assertEqual(out, 'File: mod.py:7 (ADDED)\nCommit: abc123')


if __name__ == '__main__':
    unittest.main()
