import types
import unittest
from unittest.mock import Mock, patch

from kato_core_lib.helpers.shell_status_utils import (
    clear_active_inline_status,
    sleep_with_warmup_countdown,
)


class ShellStatusUtilsTests(unittest.TestCase):
    def test_clear_active_inline_status_uses_active_spinner_stream(self) -> None:
        class _Stream:
            def __init__(self) -> None:
                self.chunks: list[str] = []

            def isatty(self) -> bool:
                return True

            def write(self, chunk: str) -> None:
                self.chunks.append(chunk)

            def flush(self) -> None:
                return None

        stream = _Stream()
        spinner = None

        from kato_core_lib.helpers import shell_status_utils

        try:
            spinner = shell_status_utils.InlineStatusSpinner(
                'Validating connection (3/3): openhands',
                stream=stream,
            )
            shell_status_utils._ACTIVE_INLINE_STATUS_SPINNER = spinner
            clear_active_inline_status()
        finally:
            shell_status_utils._ACTIVE_INLINE_STATUS_SPINNER = None

        self.assertEqual(
            stream.chunks,
            [
                '\r'
                + (' ' * (len('Validating connection (3/3): openhands') + 2))
                + '\r'
            ],
        )

    def test_inline_status_spinner_persists_final_line_without_spinner(self) -> None:
        class _Stream:
            def __init__(self) -> None:
                self.chunks: list[str] = []

            def isatty(self) -> bool:
                return True

            def write(self, chunk: str) -> None:
                self.chunks.append(chunk)

            def flush(self) -> None:
                return None

        stream = _Stream()
        spinner = None

        from kato_core_lib.helpers import shell_status_utils

        try:
            spinner = shell_status_utils.InlineStatusSpinner(
                'Validating connection (3/3): openhands',
                stream=stream,
                persist_final_line=True,
            )
            spinner._thread = Mock()
            spinner._current_status_text = Mock(return_value='Validating connection (3/3): openhands')
            spinner.stop()
        finally:
            shell_status_utils._ACTIVE_INLINE_STATUS_SPINNER = None

        self.assertEqual(
            stream.chunks,
            ['\rValidating connection (3/3): openhands\n'],
        )

    def test_sleep_with_warmup_countdown_updates_inline_status_for_tty_stream(self) -> None:
        sleep_calls: list[float] = []

        class _Stream:
            def __init__(self) -> None:
                self.chunks: list[str] = []

            def isatty(self) -> bool:
                return True

            def write(self, chunk: str) -> None:
                self.chunks.append(chunk)

            def flush(self) -> None:
                return None

        stream = _Stream()

        sleep_with_warmup_countdown(
            1.05,
            sleep_fn=lambda seconds: sleep_calls.append(seconds),
            stream=stream,
        )

        self.assertEqual(sleep_calls[:5], [0.2, 0.2, 0.2, 0.2, 0.2])
        self.assertAlmostEqual(sleep_calls[5], 0.05)
        self.assertEqual(
            stream.chunks,
            [
                '\rWaiting 2 seconds for Kato to warm up before scanning tasks /',
                '\rWaiting 1 second for Kato to warm up before scanning tasks -',
                '\rWaiting 1 second for Kato to warm up before scanning tasks \\',
                '\rWaiting 1 second for Kato to warm up before scanning tasks |',
                '\rWaiting 1 second for Kato to warm up before scanning tasks /',
                '\rWaiting 1 second for Kato to warm up before scanning tasks -',
                '\r'
                + (' ' * (len('Waiting 999 seconds for Kato to warm up before scanning tasks /') + 2))
                + '\r',
            ],
        )
