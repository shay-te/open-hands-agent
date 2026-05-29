import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from claude_core_lib.claude_core_lib.helpers.effort_levels import (
    FALLBACK_EFFORT_LEVELS,
    discover_effort_levels,
    reset_effort_levels_cache,
)

_HELP = (
    '  --model <model>      The model\n'
    '  --effort <level>     Effort level for the current session '
    '(low, medium, high, xhigh, max)\n'
    '  --verbose            Verbose\n'
)


class DiscoverEffortLevelsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_effort_levels_cache()
        self.addCleanup(reset_effort_levels_cache)

    def _run(self, stdout='', stderr='', side_effect=None):
        if side_effect is not None:
            return patch.object(subprocess, 'run', side_effect=side_effect)
        return patch.object(
            subprocess, 'run',
            return_value=SimpleNamespace(stdout=stdout, stderr=stderr, returncode=0),
        )

    def test_parses_levels_from_help(self) -> None:
        with self._run(stdout=_HELP):
            self.assertEqual(
                discover_effort_levels('claude'),
                ['low', 'medium', 'high', 'xhigh', 'max'],
            )

    def test_parses_from_stderr_too(self) -> None:
        with self._run(stderr=_HELP):
            self.assertEqual(discover_effort_levels('claude')[0], 'low')

    def test_falls_back_when_binary_missing(self) -> None:
        with self._run(side_effect=FileNotFoundError('no claude')):
            self.assertEqual(discover_effort_levels('claude'), list(FALLBACK_EFFORT_LEVELS))

    def test_falls_back_when_flag_absent(self) -> None:
        with self._run(stdout='  --model <model>   The model\n'):
            self.assertEqual(discover_effort_levels('claude'), list(FALLBACK_EFFORT_LEVELS))

    def test_result_is_cached_per_binary(self) -> None:
        with self._run(stdout=_HELP) as run:
            discover_effort_levels('claude')
            discover_effort_levels('claude')
            self.assertEqual(run.call_count, 1)

    def test_ignores_garbage_tokens(self) -> None:
        with self._run(stdout='  --effort <level>  (low, , 123!!, high)\n'):
            self.assertEqual(discover_effort_levels('claude'), ['low', 'high'])


if __name__ == '__main__':
    unittest.main()
