from __future__ import annotations

import logging
import re
from pathlib import Path

from agent_core_lib.agent_core_lib.helpers.cached_file_render import (
    cached_file_render,
)

_TIMESTAMP_PATTERN = re.compile(r'^<!-- last_compacted:.*-->$')
_MAX_BODY_CHARS = 50_000

_LESSONS_DIRECTIVE_TEMPLATE = (
    'Codebase-specific lessons learned across previous tasks '
    '(location: {path}). These are concrete rules extracted from real '
    'mistakes that happened on prior tasks in this codebase. Treat '
    'them as additional constraints on your work — alongside the '
    'task description, not in conflict with it. If a lesson seems '
    'irrelevant to the current task, ignore it; do not invent work to '
    'satisfy a rule that does not apply.\n'
    '\n'
    '--- BEGIN LEARNED LESSONS ---\n'
    '{text}\n'
    '--- END LEARNED LESSONS ---\n'
)


def read_lessons_file(
    path: str,
    *,
    logger: logging.Logger | None = None,
) -> str:
    def render(file_path: Path) -> str:
        try:
            raw = file_path.read_text(encoding='utf-8')
        except OSError as exc:
            if logger is not None:
                logger.warning('failed to read lessons file at %s: %s', file_path, exc)
            return ''
        body = _strip_timestamp_header(raw).strip()
        if not body:
            return ''
        if len(body) > _MAX_BODY_CHARS:
            body = body[:_MAX_BODY_CHARS]
        return _LESSONS_DIRECTIVE_TEMPLATE.format(path=str(file_path), text=body)

    return cached_file_render(path, render, logger=logger)


def _strip_timestamp_header(text: str) -> str:
    if not text:
        return ''
    lines = text.splitlines()
    if lines and _TIMESTAMP_PATTERN.match(lines[0]):
        return '\n'.join(lines[1:]).lstrip('\n')
    return text
