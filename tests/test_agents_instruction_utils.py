import tempfile
import types
import unittest
from pathlib import Path

from agent_core_lib.agent_core_lib.helpers.agents_instruction_utils import repository_agents_instructions_text


class AgentsInstructionUtilsTests(unittest.TestCase):
    def test_collects_all_repository_agents_files_with_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'AGENTS.md').write_text('root rules\n', encoding='utf-8')
            (root / 'src').mkdir()
            (root / 'src' / 'AGENTS.md').write_text('src rules\n', encoding='utf-8')
            (root / '.git').mkdir()
            (root / '.git' / 'AGENTS.md').write_text('ignored\n', encoding='utf-8')
            repository = types.SimpleNamespace(id='client', local_path=str(root))

            result = repository_agents_instructions_text([repository])

        self.assertIn('Repository AGENTS.md instructions:', result)
        self.assertIn('Repository client at', result)
        self.assertIn('AGENTS.md:\nroot rules', result)
        self.assertIn('src/AGENTS.md:\nsrc rules', result)
        self.assertNotIn('ignored', result)
        self.assertIn('deeper files are more specific', result)
        self.assertIn('Orchestration layer safety, allowed-repository, forbidden-repository', result)

    def test_returns_empty_text_when_repository_has_no_agents_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = types.SimpleNamespace(id='client', local_path=tmp)

            result = repository_agents_instructions_text([repository])

        self.assertEqual(result, '')


if __name__ == '__main__':
    unittest.main()
