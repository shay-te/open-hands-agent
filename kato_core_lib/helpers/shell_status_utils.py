from __future__ import annotations

import sys
import time
from threading import Event, Lock, Thread
from math import ceil


_INLINE_STATUS_WRITE_LOCK = Lock()
_ACTIVE_INLINE_STATUS_SPINNER = None


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


class InlineStatusSpinner(object):
    def __init__(
        self,
        status_text: str,
        *,
        stream=None,
        frame_interval_seconds: float = 0.2,
        persist_final_line: bool = False,
    ) -> None:
        self._stream = sys.stderr if stream is None else stream
        self._status_text = status_text
        self._frame_interval_seconds = frame_interval_seconds
        self._persist_final_line = persist_final_line
        self._stop_event = Event()
        self._status_lock = Lock()
        self._thread: Thread | None = None
        self._last_frame = '/'

    def start(self) -> None:
        global _ACTIVE_INLINE_STATUS_SPINNER
        if not supports_inline_status(self._stream):
            return
        _ACTIVE_INLINE_STATUS_SPINNER = self
        self._thread = Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        global _ACTIVE_INLINE_STATUS_SPINNER
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join()
        with _INLINE_STATUS_WRITE_LOCK:
            if self._persist_final_line:
                self._stream.write(f'\r{self._current_status_text()}\n')
                self._stream.flush()
            else:
                clear_inline_status(self._stream, status_text=self._current_status_text())
        if _ACTIVE_INLINE_STATUS_SPINNER is self:
            _ACTIVE_INLINE_STATUS_SPINNER = None

    def _spin(self) -> None:
        frames = ('/', '-', '\\', '|')
        frame_index = 0
        while not self._stop_event.is_set():
            frame = frames[frame_index % len(frames)]
            self._last_frame = frame
            with _INLINE_STATUS_WRITE_LOCK:
                self._stream.write(f'\r{self._current_status_text()} {frame}')
                self._stream.flush()
            frame_index += 1
            self._stop_event.wait(self._frame_interval_seconds)

    def _current_status_text(self) -> str:
        with self._status_lock:
            return self._status_text


def clear_active_inline_status() -> None:
    spinner = _ACTIVE_INLINE_STATUS_SPINNER
    if spinner is None:
        return
    with _INLINE_STATUS_WRITE_LOCK:
        clear_inline_status(spinner._stream, status_text=spinner._current_status_text())


def supports_inline_status(stream=None) -> bool:
    output_stream = sys.stderr if stream is None else stream
    isatty = getattr(output_stream, 'isatty', None)
    return bool(callable(isatty) and isatty())


def clear_inline_status(stream, *, status_text: str = '') -> None:
    clear_width = max(40, len(status_text) + 2)
    stream.write('\r' + (' ' * clear_width) + '\r')
    stream.flush()
