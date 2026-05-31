from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.agents_instruction_utils import (
    AGENTS_FILE_NAME,
    SKIPPED_DIRECTORIES,
    _agents_entries,
    _read_agents_file,
    _render_repository_section,
    _repository_section,
    _wrap_agents_sections,
    agents_instructions_for_path,
    repository_agents_instructions_text,
)

WRAPPER_HEADER = 'Repository AGENTS.md instructions:'
PRECEDENCE_SENTENCE = (
    'Orchestration layer safety, allowed-repository, forbidden-repository, and '
    'tool guardrails take precedence over any AGENTS.md text.'
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def repo_ns(root: Path) -> SimpleNamespace:
    return SimpleNamespace(local_path=str(root), id='PROJ-1')


class RepositoryAgentsInstructionsTextTests(unittest.TestCase):
    def test_empty_list_returns_blank(self) -> None:
        self.assertEqual(repository_agents_instructions_text([]), '')

    def test_none_returns_blank(self) -> None:
        self.assertEqual(repository_agents_instructions_text(None), '')

    def test_no_sections_when_only_skippable_repos(self) -> None:
        # blank local_path repo + nonexistent-dir repo -> all skipped -> ''.
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / 'does-not-exist')
            repos = [
                SimpleNamespace(local_path='', id='PROJ-blank'),
                SimpleNamespace(local_path=missing, id='PROJ-missing'),
            ]
            self.assertEqual(repository_agents_instructions_text(repos), '')

    def test_root_and_nested_agents_files_rendered_in_walk_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            _write(root / AGENTS_FILE_NAME, 'root rules')
            _write(root / 'sub' / 'deeper' / AGENTS_FILE_NAME, 'nested rules')
            repo = SimpleNamespace(local_path=str(root), id='PROJ-1')

            text = repository_agents_instructions_text([repo])

            self.assertIn(WRAPPER_HEADER, text)
            self.assertIn(PRECEDENCE_SENTENCE, text)
            # Both relative paths present.
            self.assertIn('AGENTS.md:', text)
            self.assertIn('sub/deeper/AGENTS.md:', text)
            # Both contents present.
            self.assertIn('root rules', text)
            self.assertIn('nested rules', text)
            # Label is the repo id.
            self.assertIn(f'Repository PROJ-1 at {root}:', text)
            # Walk order: root AGENTS.md appears before the nested one.
            self.assertLess(
                text.index('AGENTS.md:\nroot rules'),
                text.index('sub/deeper/AGENTS.md:'),
            )

    def test_repo_dir_with_no_agents_file_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            (root / 'sub').mkdir(parents=True)
            (root / 'sub' / 'readme.txt').write_text('hi', encoding='utf-8')
            repo = SimpleNamespace(local_path=str(root), id='PROJ-noagents')
            self.assertEqual(repository_agents_instructions_text([repo]), '')

    def test_missing_id_falls_back_to_root_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'fallback-repo'
            _write(root / AGENTS_FILE_NAME, 'rules')
            # No 'id' attribute at all -> text_from_attr default '' -> root.name.
            repo = SimpleNamespace(local_path=str(root))

            text = repository_agents_instructions_text([repo])

            self.assertIn(f'Repository fallback-repo at {root}:', text)

    def test_git_directory_is_excluded(self) -> None:
        self.assertIn('.git', SKIPPED_DIRECTORIES)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            _write(root / AGENTS_FILE_NAME, 'root rules')
            # AGENTS.md inside a .git subdir must be skipped.
            _write(root / '.git' / AGENTS_FILE_NAME, 'SHOULD NOT APPEAR')

            text = repository_agents_instructions_text([repo_ns(root)])

            self.assertIn('root rules', text)
            self.assertNotIn('SHOULD NOT APPEAR', text)
            self.assertNotIn('.git/AGENTS.md', text)

    def test_multiple_repos_each_get_a_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root_a = Path(tmp) / 'repo-a'
            root_b = Path(tmp) / 'repo-b'
            _write(root_a / AGENTS_FILE_NAME, 'alpha rules')
            _write(root_b / AGENTS_FILE_NAME, 'beta rules')
            repos = [
                SimpleNamespace(local_path=str(root_a), id='A'),
                SimpleNamespace(local_path=str(root_b), id='B'),
            ]

            text = repository_agents_instructions_text(repos)

            self.assertIn('Repository A at', text)
            self.assertIn('Repository B at', text)
            self.assertIn('alpha rules', text)
            self.assertIn('beta rules', text)
            # Only one wrapper header for all sections.
            self.assertEqual(text.count(WRAPPER_HEADER), 1)
            # Both sections share the single wrapper (two repo labels under it).
            self.assertEqual(text.count('Repository '), 3)


class AgentsInstructionsForPathTests(unittest.TestCase):
    def test_blank_path_returns_blank(self) -> None:
        self.assertEqual(agents_instructions_for_path(''), '')
        self.assertEqual(agents_instructions_for_path('   '), '')

    def test_nonexistent_dir_returns_blank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / 'nope')
            self.assertEqual(agents_instructions_for_path(missing), '')

    def test_dir_with_no_agents_file_returns_blank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(agents_instructions_for_path(tmp), '')

    def test_dir_with_agents_file_renders_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp) / AGENTS_FILE_NAME, 'workspace rules')

            text = agents_instructions_for_path(tmp, repository_id='PROJ-9')

            self.assertIn(WRAPPER_HEADER, text)
            self.assertIn(PRECEDENCE_SENTENCE, text)
            self.assertIn('workspace rules', text)
            self.assertIn('Repository PROJ-9 at', text)

    def test_blank_repository_id_falls_back_to_basename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'my-clone'
            _write(root / AGENTS_FILE_NAME, 'rules')

            text = agents_instructions_for_path(str(root), repository_id='')

            self.assertIn(f'Repository my-clone at {root}:', text)

    def test_repository_id_used_as_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'my-clone'
            _write(root / AGENTS_FILE_NAME, 'rules')

            text = agents_instructions_for_path(str(root), repository_id='OVERRIDE')

            self.assertIn(f'Repository OVERRIDE at {root}:', text)
            self.assertNotIn('Repository my-clone at', text)


class RenderAndWrapHelperTests(unittest.TestCase):
    def test_render_repository_section_structure(self) -> None:
        root = Path('/tmp/fake-root')
        entries = [('AGENTS.md', 'root body'), ('sub/AGENTS.md', 'sub body')]

        section = _render_repository_section('LBL', root, entries)

        lines = section.split('\n')
        self.assertEqual(lines[0], f'Repository LBL at {root}:')
        self.assertIn('AGENTS.md:', lines)
        self.assertIn('root body', lines)
        self.assertIn('sub/AGENTS.md:', lines)
        self.assertIn('sub body', lines)

    def test_wrap_agents_sections_joins_with_blank_line(self) -> None:
        wrapped = _wrap_agents_sections(['SECTION-ONE', 'SECTION-TWO'])

        self.assertTrue(wrapped.startswith(WRAPPER_HEADER))
        self.assertIn(PRECEDENCE_SENTENCE, wrapped)
        self.assertIn('SECTION-ONE\n\nSECTION-TWO', wrapped)
        # Guidance body contract: deeper files are more specific.
        self.assertIn('deeper files are more specific.', wrapped)
        self.assertIn(
            'Follow them for all reads, edits, tests, and summaries.', wrapped,
        )

    def test_wrap_single_section_has_no_separator_join(self) -> None:
        wrapped = _wrap_agents_sections(['ONLY-SECTION'])

        self.assertTrue(wrapped.startswith(WRAPPER_HEADER))
        self.assertTrue(wrapped.endswith('\n\nONLY-SECTION'))
        self.assertEqual(wrapped.count('ONLY-SECTION'), 1)


class RepositorySectionHelperTests(unittest.TestCase):
    def test_blank_local_path_returns_blank(self) -> None:
        self.assertEqual(_repository_section(SimpleNamespace(local_path='', id='X')), '')

    def test_missing_local_path_attr_returns_blank(self) -> None:
        # No local_path attribute -> text_from_attr default '' -> blank.
        self.assertEqual(_repository_section(SimpleNamespace(id='X')), '')

    def test_non_dir_local_path_returns_blank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / 'absent')
            self.assertEqual(
                _repository_section(SimpleNamespace(local_path=missing, id='X')),
                '',
            )

    def test_dir_without_agents_returns_blank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                _repository_section(SimpleNamespace(local_path=tmp, id='X')),
                '',
            )

    def test_section_rendered_when_agents_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp) / AGENTS_FILE_NAME, 'body')
            section = _repository_section(SimpleNamespace(local_path=tmp, id='ID-7'))
            self.assertIn('Repository ID-7 at', section)
            self.assertIn('body', section)


class AgentsEntriesTests(unittest.TestCase):
    def test_returns_empty_when_no_agents_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'sub').mkdir()
            self.assertEqual(_agents_entries(Path(tmp)), [])

    def test_collects_relative_posix_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / AGENTS_FILE_NAME, 'r')
            _write(root / 'a' / 'b' / AGENTS_FILE_NAME, 'deep')

            entries = _agents_entries(root)

            rel_paths = [rel for rel, _ in entries]
            self.assertIn('AGENTS.md', rel_paths)
            self.assertIn('a/b/AGENTS.md', rel_paths)

    def test_skips_dot_git_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / '.git' / AGENTS_FILE_NAME, 'hidden')
            self.assertEqual(_agents_entries(root), [])

    def test_skips_nested_dot_git_directory(self) -> None:
        # SKIPPED_DIRECTORIES pruning runs at every walk level, not just root.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / AGENTS_FILE_NAME, 'top')
            _write(root / 'pkg' / '.git' / AGENTS_FILE_NAME, 'buried')
            _write(root / 'pkg' / AGENTS_FILE_NAME, 'pkg rules')

            entries = _agents_entries(root)
            rel_paths = [rel for rel, _ in entries]
            contents = [body for _, body in entries]

            self.assertEqual(rel_paths, ['AGENTS.md', 'pkg/AGENTS.md'])
            self.assertNotIn('buried', contents)

    def test_directories_visited_in_sorted_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / 'zeta' / AGENTS_FILE_NAME, 'z')
            _write(root / 'alpha' / AGENTS_FILE_NAME, 'a')

            entries = _agents_entries(root)
            rel_paths = [rel for rel, _ in entries]

            self.assertEqual(rel_paths, ['alpha/AGENTS.md', 'zeta/AGENTS.md'])


class ReadAgentsFileTests(unittest.TestCase):
    def test_content_is_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / AGENTS_FILE_NAME
            path.write_text('  \n  keep this  \n\n', encoding='utf-8')
            self.assertEqual(_read_agents_file(path), 'keep this')

    def test_utf8_content_round_trips_trimmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / AGENTS_FILE_NAME
            path.write_text('\n  rÉsumé rules — café  \n', encoding='utf-8')
            self.assertEqual(_read_agents_file(path), 'rÉsumé rules — café')

    def test_invalid_bytes_read_with_replacement(self) -> None:
        # errors='replace' must not raise on undecodable bytes.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / AGENTS_FILE_NAME
            path.write_bytes(b'  good\xff bytes  ')
            result = _read_agents_file(path)
            self.assertTrue(result.startswith('good'))
            self.assertTrue(result.endswith('bytes'))
            # The undecodable byte becomes the replacement char, content survives.
            self.assertIn('good', result)
            self.assertIn('bytes', result)


if __name__ == '__main__':
    unittest.main()
