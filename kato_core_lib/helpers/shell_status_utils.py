from __future__ import annotations

import sys
import time
from math import ceil


def _sleep_with_inline_spinner(
    total_seconds: float,
    *,
    render_frame,
    clear_text: str,
    sleep_fn=time.sleep,
    stream=None,
) -> None:
    """Sleep for ``total_seconds`` while animating an inline spinner.

    ``render_frame(frame, remaining_seconds, frame_index)`` returns the
    text written after ``\\r`` each tick; ``clear_text`` sizes the final
    clear. Falls back to a plain ``sleep_fn`` when the stream can't show
    inline status, and is a no-op for non-positive durations.
    """
    if total_seconds <= 0:
        return

    output_stream = sys.stderr if stream is None else stream
    if not supports_inline_status(output_stream):
        sleep_fn(total_seconds)
        return

    frames = ('/', '-', '\\', '|')
    frame_interval_seconds = 0.2
    remaining_seconds = float(total_seconds)
    frame_index = 0
    while remaining_seconds > 0:
        frame = frames[frame_index % len(frames)]
        output_stream.write(
            '\r' + render_frame(frame, remaining_seconds, frame_index)
        )
        output_stream.flush()
        sleep_duration = min(frame_interval_seconds, remaining_seconds)
        sleep_fn(sleep_duration)
        remaining_seconds -= sleep_duration
        frame_index += 1
    clear_inline_status(output_stream, status_text=clear_text)


def sleep_with_countdown_spinner(
    total_seconds: float,
    *,
    status_text: str,
    sleep_fn=time.sleep,
    stream=None,
    countdown_seconds: int | None = None,
) -> None:
    """Sleep for ``total_seconds`` while spinning an inline countdown.

    By default the displayed number is derived from the remaining sleep,
    so a 30s sleep ticks 30→1. Callers that drive their own outer loop
    (e.g. ``_idle_with_heartbeat``) sleep in ~1s chunks but want the
    spinner to show the *outer* countdown — pass ``countdown_seconds`` to
    pin the displayed value for this call.
    """
    def render(frame, remaining_seconds, frame_index):
        if countdown_seconds is None:
            displayed = max(1, int(ceil(remaining_seconds)))
        else:
            displayed = max(0, int(countdown_seconds))
        return f'{status_text} {frame} {displayed}'

    _sleep_with_inline_spinner(
        total_seconds,
        render_frame=render,
        clear_text=f'{status_text} 999',
        sleep_fn=sleep_fn,
        stream=stream,
    )


def sleep_with_warmup_countdown(
    total_seconds: float,
    *,
    sleep_fn=time.sleep,
    stream=None,
) -> None:
    def render(frame, remaining_seconds, frame_index):
        countdown_seconds = max(1, int(ceil(remaining_seconds)))
        seconds_label = 'second' if countdown_seconds == 1 else 'seconds'
        return (
            f'Waiting {countdown_seconds} {seconds_label} for Kato to warm up '
            f'before scanning tasks {frame}'
        )

    _sleep_with_inline_spinner(
        total_seconds,
        render_frame=render,
        clear_text='Waiting 999 seconds for Kato to warm up before scanning tasks /',
        sleep_fn=sleep_fn,
        stream=stream,
    )


def supports_inline_status(stream=None) -> bool:
    output_stream = sys.stderr if stream is None else stream
    isatty = getattr(output_stream, 'isatty', None)
    return bool(callable(isatty) and isatty())


def clear_inline_status(stream, *, status_text: str = '') -> None:
    clear_width = max(40, len(status_text) + 2)
    stream.write('\r' + (' ' * clear_width) + '\r')
    stream.flush()
