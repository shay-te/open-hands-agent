"""Pin the agent-provider contract surface.

Two reasons for these tests: (a) drift guard so a future tweak to
``AgentProvider`` doesn't silently change the method names existing
backends must implement, (b) document by example what a minimal
backend looks like.
"""

from __future__ import annotations

import unittest

from agent_provider_contracts.agent_provider_contracts.agent_provider import (
    AgentProvider,
)
from agent_provider_contracts.agent_provider_contracts.agent_result import AgentResult
from agent_provider_contracts.agent_provider_contracts.agent_review_comment import (
    AgentReviewComment,
)
from agent_provider_contracts.agent_provider_contracts.agent_task import AgentTask
from agent_provider_contracts.agent_provider_contracts.prepared_task_context import (
    AgentPreparedTaskContext,
)


class _MinimalCompliantBackend(object):
    """Smallest object that satisfies ``AgentProvider`` — used to
    prove the runtime-checkable Protocol accepts a duck-typed
    implementation without inheritance, which is the on-ramp
    new backends rely on."""

    def validate_connection(self) -> None:
        return None

    def validate_model_access(self) -> None:
        return None

    def implement_task(self, task, agent_session_id='', prepared_task=None):
        return {'success': True}

    def test_task(self, task, prepared_task=None):
        return {'success': True}

    def fix_review_comment(self, comment, branch_name, agent_session_id='', task_id='', task_summary=''):
        return {'success': True}

    def fix_review_comments(self, comments, branch_name, agent_session_id='', task_id='', task_summary='', mode='fix'):
        return {'success': True}

    def delete_conversation(self, conversation_id):
        return None

    def stop_all_conversations(self):
        return None


class _MissingMethodBackend(object):
    """Lacks ``fix_review_comments`` so the runtime check fails.

    Pinned because it's the kind of partial implementation a new
    backend might submit while still wiring up; the Protocol must
    refuse it loudly rather than silently accept a half-finished
    impl that crashes at first review-comment fix.
    """

    def validate_connection(self): return None
    def validate_model_access(self): return None
    def implement_task(self, task, agent_session_id='', prepared_task=None): return {}
    def test_task(self, task, prepared_task=None): return {}
    def fix_review_comment(self, comment, branch_name, agent_session_id='', task_id='', task_summary=''): return {}
    # NOTE: fix_review_comments deliberately missing
    def delete_conversation(self, conversation_id): return None
    def stop_all_conversations(self): return None


class AgentProviderProtocolTests(unittest.TestCase):
    def test_minimal_compliant_backend_satisfies_protocol(self) -> None:
        # ``runtime_checkable`` Protocol → isinstance check must
        # pass for a duck-typed implementation. This is the on-ramp
        # new backends use (no required base class).
        self.assertIsInstance(_MinimalCompliantBackend(), AgentProvider)

    def test_missing_method_backend_does_not_satisfy_protocol(self) -> None:
        # Pinning so a backend that forgets one method gets caught
        # at boot (when kato wires the factory) instead of at the
        # first call to the missing method.
        self.assertNotIsInstance(_MissingMethodBackend(), AgentProvider)

    def test_protocol_lists_exactly_the_eight_required_methods(self) -> None:
        # The eight methods the existing two backends already share.
        # Bumping this list means every backend has to implement
        # whatever was added — so we pin it to make the breaking
        # change visible in code review.
        expected = {
            'validate_connection',
            'validate_model_access',
            'implement_task',
            'test_task',
            'fix_review_comment',
            'fix_review_comments',
            'delete_conversation',
            'stop_all_conversations',
        }
        actual = {
            name for name in dir(AgentProvider)
            if not name.startswith('_') and callable(getattr(AgentProvider, name))
        }
        self.assertEqual(actual, expected)


class AgentTaskDTOTests(unittest.TestCase):
    def test_defaults_are_safe_for_a_blank_task(self) -> None:
        # A backend constructing AgentTask() with no args must not
        # blow up — used by tests + by the DTO converters in
        # ``claude_core_lib`` / ``openhands_core_lib`` for fallback
        # paths when kato hands them a partially-populated task.
        task = AgentTask()
        self.assertEqual(task.id, '')
        self.assertEqual(task.summary, '')
        self.assertEqual(task.description, '')
        self.assertEqual(task.repositories, [])

    def test_dto_is_immutable(self) -> None:
        # ``frozen=True`` is load-bearing: backends pass tasks
        # through their async layers + retry loops, and accidental
        # mutation would be a heisenbug to track down.
        task = AgentTask(id='UNA-1')
        with self.assertRaises(Exception):
            task.id = 'UNA-2'  # type: ignore[misc]


class AgentReviewCommentDTOTests(unittest.TestCase):
    def test_defaults_match_blank_comment(self) -> None:
        comment = AgentReviewComment()
        self.assertEqual(comment.comment_id, '')
        self.assertEqual(comment.body, '')
        self.assertEqual(comment.all_comments, [])

    def test_line_number_accepts_int_or_blank_string(self) -> None:
        # Mirrors what kato's ReviewComment carries today: int when
        # the platform reports a line, '' when it's a file-level
        # comment. Pinning so the contract doesn't accidentally
        # narrow to int-only and break file-level comments.
        AgentReviewComment(line_number=42)
        AgentReviewComment(line_number='')


class AgentResultAliasTests(unittest.TestCase):
    def test_dict_with_success_bool_satisfies_alias(self) -> None:
        # AgentResult is a typing alias for dict[str, Any]; the only
        # contract the kato call sites read is ``success: bool``.
        # This test is mostly a hint for future readers of what the
        # backends always populate.
        result: AgentResult = {'success': True, 'message': 'done'}
        self.assertTrue(result['success'])


class AgentPreparedTaskContextDTOTests(unittest.TestCase):
    def test_blank_context_has_safe_defaults(self) -> None:
        ctx = AgentPreparedTaskContext()
        self.assertEqual(ctx.branch_name, '')
        self.assertEqual(ctx.branches_by_repository, {})
        self.assertEqual(ctx.repositories, [])
        self.assertEqual(ctx.cwd, '')


if __name__ == '__main__':
    unittest.main()
