"""Tests for ``kato_core_lib.helpers.kato_tag_utils`` — the builders/parsers
for kato's ``kato:`` namespaced task tags, plus a guard that every tag
constant in ``fields`` is actually derived from the one namespace.
"""
import unittest

from kato_core_lib.data_layers.data.fields import (
    KATO_TAG_NAMESPACE,
    RepositoryFields,
    TaskTags,
    TRIAGE_OUTCOME_TAGS,
)
from kato_core_lib.helpers.kato_tag_utils import (
    build_repository_tag,
    build_triage_tag,
    repository_id_from_tag,
)


class NamespaceDerivationTests(unittest.TestCase):
    """Every kato tag constant must be built from the single namespace, and
    keep its historical string value (operators' existing tags must match)."""

    def test_namespace_value(self):
        self.assertEqual(KATO_TAG_NAMESPACE, 'kato')

    def test_repository_constants_unchanged(self):
        self.assertEqual(RepositoryFields.REPOSITORY_TAG_SEGMENT, 'repo')
        self.assertEqual(RepositoryFields.REPOSITORY_TAG_PREFIX, 'kato:repo:')

    def test_task_tag_constants_unchanged(self):
        self.assertEqual(TaskTags.WAIT_PLANNING, 'kato:wait-planning')
        self.assertEqual(TaskTags.WAIT_BEFORE_GIT_PUSH, 'kato:wait-before-git-push')
        self.assertEqual(TaskTags.TRIAGE_PREFIX, 'kato:triage:')
        self.assertEqual(TaskTags.TRIAGE_INVESTIGATE, 'kato:triage:investigate')
        self.assertEqual(TaskTags.TRIAGE_CRITICAL, 'kato:triage:critical')
        self.assertEqual(TaskTags.TRIAGE_NEEDS_INFO, 'kato:triage:needs-info')
        self.assertEqual(TaskTags.TRIAGE_QUESTION, 'kato:triage:question')

    def test_every_tag_constant_starts_with_namespace(self):
        tags = [
            RepositoryFields.REPOSITORY_TAG_PREFIX,
            TaskTags.WAIT_PLANNING,
            TaskTags.WAIT_BEFORE_GIT_PUSH,
            TaskTags.TRIAGE_PREFIX,
            *TRIAGE_OUTCOME_TAGS,
        ]
        for tag in tags:
            self.assertTrue(
                tag.startswith(f'{KATO_TAG_NAMESPACE}:'),
                f'{tag!r} is not under the kato: namespace',
            )


class BuildRepositoryTagTests(unittest.TestCase):
    def test_builds_prefixed_tag(self):
        self.assertEqual(build_repository_tag('my-backend'), 'kato:repo:my-backend')

    def test_trims_whitespace_but_keeps_case(self):
        self.assertEqual(build_repository_tag('  Client-Alias '), 'kato:repo:Client-Alias')

    def test_blank_repo_id_yields_bare_prefix(self):
        self.assertEqual(build_repository_tag(''), 'kato:repo:')
        self.assertEqual(build_repository_tag(None), 'kato:repo:')


class RepositoryIdFromTagTests(unittest.TestCase):
    def test_strips_prefix_preserving_case(self):
        self.assertEqual(repository_id_from_tag('kato:repo:Client-Alias'), 'Client-Alias')

    def test_case_insensitive_prefix_match(self):
        self.assertEqual(repository_id_from_tag('KATO:REPO:backend'), 'backend')

    def test_empty_after_prefix_returns_blank(self):
        self.assertEqual(repository_id_from_tag('kato:repo:'), '')
        self.assertEqual(repository_id_from_tag('kato:repo:   '), '')

    def test_non_repo_tag_returns_blank(self):
        self.assertEqual(repository_id_from_tag('kato:triage:high'), '')
        self.assertEqual(repository_id_from_tag('random'), '')
        self.assertEqual(repository_id_from_tag(None), '')

    def test_round_trip_build_then_parse(self):
        for repo in ('my-backend', 'Client-Alias', 'svc_42'):
            self.assertEqual(repository_id_from_tag(build_repository_tag(repo)), repo)


class BuildTriageTagTests(unittest.TestCase):
    def test_builds_triage_tag(self):
        self.assertEqual(build_triage_tag('high'), 'kato:triage:high')

    def test_normalizes(self):
        self.assertEqual(build_triage_tag('  needs-info '), 'kato:triage:needs-info')

    def test_matches_canonical_outcome_constants(self):
        # Building each canonical outcome from its leaf must reproduce the
        # exact constant — the builder is the single way to spell them.
        for tag in TRIAGE_OUTCOME_TAGS:
            leaf = tag[len(TaskTags.TRIAGE_PREFIX):]
            self.assertEqual(build_triage_tag(leaf), tag)


if __name__ == '__main__':
    unittest.main()
