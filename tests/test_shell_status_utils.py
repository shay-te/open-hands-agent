import unittest

from kato_core_lib.helpers.shell_status_utils import (
    sleep_with_warmup_countdown,
)


class ShellStatusUtilsTests(unittest.TestCase):
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
