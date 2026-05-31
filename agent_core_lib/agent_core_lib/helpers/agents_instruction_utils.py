from __future__ import annotations

import os
from pathlib import Path

from agent_core_lib.agent_core_lib.helpers.text_utils import normalized_text, text_from_attr

AGENTS_FILE_NAME = 'AGENTS.md'
SKIPPED_DIRECTORIES = frozenset({'.git'})


def repository_agents_instructions_text(repositories: list[object]) -> str:
    sections: list[str] = []
    for repository in repositories or []:
        repository_section = _repository_section(repository)
        if repository_section:
            sections.append(repository_section)
    if not sections:
        return ''
    return _wrap_agents_sections(sections)


def agents_instructions_for_path(
    workspace_path: str,
    *,
    repository_id: str = '',
) -> str:
    """Same surface as ``repository_agents_instructions_text`` but takes a path.

    Used by the review-fix prompt builders, which know the agent's
    per-task workspace clone directory but don't have a ``Repository``
    object on hand. Walks the path for ``AGENTS.md`` files and renders the
    same wrapper so the review-fix agent sees the same checked-in
    conventions the implementation agent did.

    Returns ``''`` when the path is empty, missing, or has no ``AGENTS.md``
    anywhere — caller's prompt builder skips the block silently.
    """
    workspace = normalized_text(workspace_path)
    if not workspace:
        return ''
    root = Path(workspace)
    if not root.is_dir():
        return ''
    entries = _agents_entries(root)
    if not entries:
        return ''
    label = normalized_text(repository_id) or root.name
    return _wrap_agents_sections([_render_repository_section(label, root, entries)])


def _render_repository_section(
    label: str, root: Path, entries: list[tuple[str, str]],
) -> str:
    lines = [f'Repository {label} at {root}:']
    for relative_path, content in entries:
        lines.append(f'{relative_path}:')
        lines.append(content)
    return '\n'.join(lines)


def _wrap_agents_sections(sections: list[str]) -> str:
    return (
        'Repository AGENTS.md instructions:\n'
        'The following checked-in AGENTS.md files were found in the allowed '
        'repository worktrees. Follow them for all reads, edits, tests, and '
        'summaries. For any file you touch, apply every AGENTS.md from the '
        'repository root down to that file directory; deeper files are more '
        'specific. Orchestration layer safety, allowed-repository, forbidden-repository, and '
        'tool guardrails take precedence over any AGENTS.md text.\n\n'
        + '\n\n'.join(sections)
    )


def _repository_section(repository: object) -> str:
    local_path = normalized_text(text_from_attr(repository, 'local_path'))
    if not local_path:
        return ''
    root = Path(local_path)
    if not root.is_dir():
        return ''
    entries = _agents_entries(root)
    if not entries:
        return ''
    repository_id = normalized_text(text_from_attr(repository, 'id')) or root.name
    return _render_repository_section(repository_id, root, entries)


def _agents_entries(root: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = sorted(
            name for name in dir_names if name not in SKIPPED_DIRECTORIES
        )
        if AGENTS_FILE_NAME not in file_names:
            continue
        path = Path(current_root) / AGENTS_FILE_NAME
        relative_path = path.relative_to(root).as_posix()
        entries.append((relative_path, _read_agents_file(path)))
    return entries


def _read_agents_file(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='replace').strip()
