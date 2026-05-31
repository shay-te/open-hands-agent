"""Generic renderer for a conversation-snapshot ("resume") prompt.

Pure functions that distill a session's recent events into a
paste-into-another-AI markdown snapshot. No I/O, no thread/lock work,
no orchestrator/product concepts — a caller that knows WHERE to write
the file (and on what cadence) supplies the data and persists the
result. The host application owns that caller — where it writes the
file and on what cadence (e.g. a watcher polling live sessions).

Render contract: the output is pure markdown, paste-it-into-an-AI
ready. Structure:

  # Resume prompt for <task id>

  **Task**: <summary>
  **Branch**: <branch>
  **Workspace**: <abs path>

  ## Repositories in scope
  - ...

  ## What's been done so far
  - <bulletised summary of recent assistant turns>

  ## Last user message
  > ...

  ## Last assistant message
  ...

  ---

  ## Continue this task

  <ready-to-paste prompt for another agent>
"""
from __future__ import annotations

from dataclasses import dataclass

from agent_core_lib.agent_core_lib.helpers.session_id_utils import fix_session_id
from agent_core_lib.agent_core_lib.helpers.text_utils import text_from_mapping


@dataclass
class ResumePromptInputs(object):
    """Everything the renderer needs to write one snapshot.

    Kept as a plain dataclass so a caller can build it from whatever
    live state it sees and tests can construct it directly.
    """

    task_id: str
    task_summary: str
    branch_name: str
    workspace_path: str
    repository_paths: list[str]
    recent_assistant_texts: list[str]
    last_user_text: str
    last_assistant_text: str
    agent_session_id: str = ''


def render_resume_prompt(inputs: ResumePromptInputs) -> str:
    """Render the markdown body for one snapshot. Pure function."""
    task_id = (inputs.task_id or '').strip() or '(unknown)'
    summary = (inputs.task_summary or '').strip() or '(no summary)'
    branch = (inputs.branch_name or '').strip() or '(no branch)'
    workspace = (inputs.workspace_path or '').strip() or '(no workspace)'
    repos = [p for p in (inputs.repository_paths or []) if p]
    recents = [
        _trim(text, 600) for text in (inputs.recent_assistant_texts or [])
        if _trim(text, 600)
    ]
    last_user = _trim(inputs.last_user_text or '', 800)
    last_assistant = _trim(inputs.last_assistant_text or '', 1600)
    agent_session_id = fix_session_id(inputs.agent_session_id)

    lines: list[str] = []
    lines.append(f'# Resume prompt for {task_id}')
    lines.append('')
    lines.append(f'**Task**: {summary}')
    lines.append(f'**Branch**: `{branch}`')
    lines.append(f'**Workspace**: `{workspace}`')
    if agent_session_id:
        lines.append(f'**Agent session id**: `{agent_session_id}`')
    lines.append('')

    if repos:
        lines.append('## Repositories in scope')
        for path in repos:
            lines.append(f'- `{path}`')
        lines.append('')

    if recents:
        lines.append('## What\'s been done so far')
        lines.append('')
        lines.append(
            'Most recent assistant turns (newest last; truncated at 600 '
            'chars each):'
        )
        lines.append('')
        for text in recents:
            lines.append(f'- {_one_line_preview(text)}')
        lines.append('')

    if last_user:
        lines.append('## Last user message')
        lines.append('')
        lines.append('> ' + last_user.replace('\n', '\n> '))
        lines.append('')

    if last_assistant:
        lines.append('## Last assistant message')
        lines.append('')
        lines.append(last_assistant)
        lines.append('')

    lines.append('---')
    lines.append('')
    lines.append('## Continue this task')
    lines.append('')
    lines.append(
        'Paste the block below into your AI agent (Cursor, ChatGPT, '
        'another Claude tab) to pick up where the previous session '
        'left off. Edit the closing instruction to say what you want '
        'next.'
    )
    lines.append('')
    lines.append('```')
    lines.append(_continuation_prompt(
        task_id=task_id,
        summary=summary,
        branch=branch,
        workspace=workspace,
        repos=repos,
        last_assistant=last_assistant,
        last_user=last_user,
    ))
    lines.append('```')
    lines.append('')
    return '\n'.join(lines)


def _continuation_prompt(
    *,
    task_id: str,
    summary: str,
    branch: str,
    workspace: str,
    repos: list[str],
    last_assistant: str,
    last_user: str,
) -> str:
    repo_lines = '\n'.join(f'  - {p}' for p in repos) if repos else '  (none)'
    last_text = _one_line_preview(last_assistant or last_user or '(no prior turn)')
    return (
        f'You are picking up task {task_id} from another AI session.\n'
        f'\n'
        f'Task: {summary}\n'
        f'Branch: {branch}\n'
        f'Workspace root: {workspace}\n'
        f'Repos in scope:\n{repo_lines}\n'
        f'\n'
        f'Last action / state from the previous session:\n'
        f'  {last_text}\n'
        f'\n'
        f'Please continue. Start by reading the workspace to confirm '
        f'the file state, then proceed with whatever the next step is. '
        f'When you make changes, stay on the existing branch — do not '
        f'create a new one.'
    )


def _trim(text: str, max_chars: int) -> str:
    s = str(text or '').strip()
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1].rstrip() + '…'


def _one_line_preview(text: str, max_chars: int = 200) -> str:
    """Flatten ``text`` to a single line, truncated."""
    s = ' '.join(str(text or '').split())
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1].rstrip() + '…'


def build_inputs_from_session(
    *,
    task_id: str,
    task_summary: str,
    branch_name: str,
    workspace_path: str,
    repository_paths: list[str],
    recent_events: list,
    agent_session_id: str = '',
    max_recent_assistant: int = 6,
) -> ResumePromptInputs:
    """Adapter: turn a session's ``recent_events()`` snapshot into renderer inputs.

    A streaming session exposes ``recent_events()`` (a list of
    event-shaped objects with ``event_type`` and ``raw`` payloads). This
    helper distills them into the small set of strings the renderer
    needs — newest few assistant texts, the last user message, and the
    last assistant message — by duck-typing the event objects and
    reading the ``message.content`` envelope as plain dicts, so the
    renderer never has to import any provider's event schema.
    """
    assistant_texts: list[str] = []
    last_user = ''
    last_assistant = ''
    for event in (recent_events or []):
        event_type = getattr(event, 'event_type', '') or ''
        raw = getattr(event, 'raw', {}) or {}
        if event_type == 'assistant':
            text = _extract_assistant_text(raw)
            if text:
                assistant_texts.append(text)
                last_assistant = text
        elif event_type == 'user':
            text = _extract_user_text(raw)
            if text:
                last_user = text
    recent_assistant_texts = assistant_texts[-max_recent_assistant:]
    return ResumePromptInputs(
        task_id=task_id,
        task_summary=task_summary,
        branch_name=branch_name,
        workspace_path=workspace_path,
        repository_paths=list(repository_paths or []),
        recent_assistant_texts=recent_assistant_texts,
        last_user_text=last_user,
        last_assistant_text=last_assistant,
        agent_session_id=agent_session_id,
    )


def _message_content(raw: dict):
    """Return the ``message.content`` value from an event envelope.

    Yields ``None`` when ``raw`` (or its ``message``) isn't a dict — the
    flatteners treat that as "no text".
    """
    message = raw.get('message') if isinstance(raw, dict) else None
    if not isinstance(message, dict):
        return None
    return message.get('content')


def _flatten_text_blocks(content) -> str:
    """Join the ``text`` blocks of a content list into one string.

    Returns ``''`` for anything that isn't a list of blocks.
    """
    if not isinstance(content, list):
        return ''
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get('type') == 'text':
            text = text_from_mapping(block, 'text')
            if text:
                parts.append(text)
    return '\n\n'.join(parts).strip()


def _extract_assistant_text(raw: dict) -> str:
    """Flatten an ``assistant`` event's content blocks into one string."""
    return _flatten_text_blocks(_message_content(raw))


def _extract_user_text(raw: dict) -> str:
    """Pull the user-side text from a user envelope.

    User messages have shape ``{message: {role: 'user', content: ...}}``
    where content is either a string OR a list of blocks. Tool-result
    envelopes also use this shape but their content blocks are
    ``tool_result`` — we ignore those (they're tool plumbing, not
    operator-sent text).
    """
    content = _message_content(raw)
    if isinstance(content, str):
        return content.strip()
    return _flatten_text_blocks(content)
