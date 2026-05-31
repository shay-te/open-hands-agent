"""Write the auto-updated ``resume_prompt.md`` into a task workspace.

Owns only the Kato-specific persistence: the filename + workspace
layout (``<workspace>/resume_prompt.md``) and the atomic write. The
generic snapshot renderer (``ResumePromptInputs`` / ``render_resume_prompt``
/ ``build_inputs_from_session``) lives in
``agent_core_lib.agent_core_lib.helpers.resume_prompt_utils``; the cadence
+ lifecycle live in ``ResumePromptWatcher``.
"""
from __future__ import annotations

from pathlib import Path

from kato_core_lib.helpers.atomic_text_utils import atomic_write_text
from kato_core_lib.helpers.logging_utils import configure_logger

RESUME_PROMPT_FILENAME = 'resume_prompt.md'


def write_resume_prompt(
    workspace_path: Path | str,
    content: str,
    *,
    logger=None,
) -> bool:
    """Write ``content`` to ``<workspace>/resume_prompt.md`` atomically.

    Returns True on success, False on any I/O failure (logged at
    warning by the underlying helper). The file is created with
    ``parents=True`` if the workspace dir is missing — the operator
    might invoke the writer for a never-provisioned task.
    """
    if not workspace_path:
        return False
    target = Path(str(workspace_path)) / RESUME_PROMPT_FILENAME
    return atomic_write_text(
        target,
        content,
        logger=logger or configure_logger(__name__),
        label='resume_prompt.md',
    )
