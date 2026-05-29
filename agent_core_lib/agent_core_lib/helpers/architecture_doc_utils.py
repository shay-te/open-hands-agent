from __future__ import annotations

import logging

from agent_core_lib.agent_core_lib.helpers.cached_file_render import (
    cached_file_render,
)

_LIVING_DOC_DIRECTIVE_TEMPLATE = (
    'Project architecture document: {path}\n'
    'At the start of every task, use the Read tool to read this '
    'file. It contains the canonical map of the workspace and any '
    'non-obvious conventions, hidden contracts, gotchas, and layer '
    'boundaries the project has accumulated. Let it shape your '
    'plan.\n'
    '\n'
    'Treat it as a living document you are responsible for keeping '
    'accurate. While working, if you discover something not yet '
    'documented that would help a future agent (a non-obvious '
    'convention, a hidden contract, a gotcha, a layer boundary, a '
    '"why we do it this way"), update the file via the Edit tool — '
    'append a new sub-section under the most appropriate top-level '
    'section, or add a new section if none fits. Do not duplicate '
    'content already documented; do not restate what the code shows. '
    'The document is a navigation aid and a contract registry, not '
    'a mirror of the source. The orchestration layer commits and pushes the file (you '
    'must NEVER run git); just edit.\n'
)


def read_architecture_doc(
    path: str,
    *,
    logger: logging.Logger | None = None,
) -> str:
    return cached_file_render(
        path,
        lambda file_path: _LIVING_DOC_DIRECTIVE_TEMPLATE.format(path=str(file_path)),
        logger=logger,
        stat_error_message=(
            'architecture doc path %s is not a file; skipping context injection'
        ),
    )
