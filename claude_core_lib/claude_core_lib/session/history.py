"""Replay archived Claude CLI sessions as raw stream-json events.

Claude Code persists every conversation it runs as a JSONL file under
``~/.claude/projects/<encoded-cwd>/<session_id>.jsonl``. After a kato
restart the in-memory ``_recent_events`` buffer is empty, so the only
way to repopulate the chat is to read those JSONL files and feed them
back into the SSE backlog. This module is the read side of that
pipeline — pure I/O, no kato types — so it stays trivially testable.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Iterable


_DEFAULT_PROJECTS_ROOT = Path.home() / '.claude' / 'projects'
# Match claude_session_index — both modules walk the same store, so they
# must agree on its location when tests redirect via this env var.
_CLAUDE_SESSIONS_ROOT_ENV_KEY = 'KATO_CLAUDE_SESSIONS_ROOT'


def _default_projects_root() -> Path:
    override = os.environ.get(_CLAUDE_SESSIONS_ROOT_ENV_KEY, '').strip()
    if override:
        return Path(override).expanduser()
    return _DEFAULT_PROJECTS_ROOT


def find_session_file(
    claude_session_id: str,
    *,
    projects_root: Path | str | None = None,
) -> Path | None:
    """Locate the JSONL transcript for ``claude_session_id``.

    Walks every ``~/.claude/projects/*/`` directory; Claude's per-project
    folder name is a lossy encoding of cwd (``/``, ``_`` and ``.`` all
    become ``-``), so reconstructing it deterministically is brittle —
    globbing is the simplest robust strategy.
    """
    session_id = (claude_session_id or '').strip()
    if not session_id:
        return None
    root = Path(projects_root) if projects_root else _default_projects_root()
    if not root.is_dir():
        return None
    pattern = str(root / '*' / f'{session_id}.jsonl')
    matches = glob.glob(pattern)
    if not matches:
        return None
    return Path(matches[0])


def delete_session_file(
    claude_session_id: str,
    *,
    projects_root: Path | str | None = None,
) -> bool:
    """Delete the JSONL transcript for ``claude_session_id``.

    Used when a task is permanently forgotten (reviewer marked it
    done / closed, or the operator hit "Forget task"): the workspace
    clones and the kato session record are removed, so the Claude
    CLI transcript — which would otherwise accumulate forever under
    ``~/.claude/projects/`` — should go too.

    Returns ``True`` when a file was removed, ``False`` when there
    was nothing to delete (no id, no matching transcript) or the
    unlink failed. Best-effort + never raises: a leftover transcript
    is a disk-space nuisance, not a reason to blow up the
    done-cleanup loop.
    """
    path = find_session_file(claude_session_id, projects_root=projects_root)
    if path is None:
        return False
    try:
        path.unlink()
        return True
    except OSError:
        # FileNotFoundError is an OSError subclass — a transcript
        # that vanished between find + unlink is fine, just report
        # "nothing removed".
        return False


def find_session_id_for_cwd(
    cwd: str | Path,
    *,
    projects_root: Path | str | None = None,
) -> str:
    """Return the most-recent Claude session id whose ``cwd`` matches.

    Used by the workspace-recovery flow: when an orphan task folder has
    no ``.kato-meta.json`` we still want to attach Claude's existing
    transcript. Claude records each turn's ``cwd`` inside the JSONL, so
    we walk every transcript under ``~/.claude/projects/*`` and pick
    the freshest one whose first datum points at ``cwd``. Returns ''
    when no session matches.
    """
    target = str(cwd or '').strip()
    if not target:
        return ''
    root = Path(projects_root) if projects_root else _DEFAULT_PROJECTS_ROOT
    if not root.is_dir():
        return ''
    matches: list[tuple[float, str]] = []
    for jsonl_path in root.glob('*/*.jsonl'):
        recorded_cwd, recorded_session_id = _peek_session_metadata(jsonl_path)
        if not recorded_session_id:
            continue
        if not _paths_equivalent(recorded_cwd, target):
            continue
        try:
            mtime = jsonl_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        matches.append((mtime, recorded_session_id))
    if not matches:
        return ''
    matches.sort(reverse=True)
    return matches[0][1]


def _peek_session_metadata(path: Path) -> tuple[str, str]:
    """Return ``(cwd, session_id)`` from the first record that has them.

    The first few lines are usually queue-ops without cwd; we read until
    we see a ``user``/``assistant`` record (which carries both fields)
    or give up after a few lines so we don't slurp giant transcripts.
    """
    try:
        with path.open('r', encoding='utf-8') as fh:
            for index, raw_line in enumerate(fh):
                if index >= 20:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                cwd = str(payload.get('cwd', '') or '').strip()
                session_id = str(payload.get('sessionId', '') or '').strip()
                if cwd and session_id:
                    return cwd, session_id
    except OSError:
        pass
    return '', ''


def _paths_equivalent(left: str, right: str) -> bool:
    if not left or not right:
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return left.rstrip('/') == right.rstrip('/')


def load_history_events(
    claude_session_id: str,
    *,
    projects_root: Path | str | None = None,
    max_events: int = 5000,
) -> list[dict]:
    """Read the JSONL transcript and return UI-friendly raw events.

    Filters out Claude-internal noise (queue ops, attachment metadata,
    summary records) so the chat shows just the conversation. Each
    returned dict has the same shape kato emits over the live stream:
    ``{'type': 'user'|'assistant'|'system'|..., 'message': {...}, ...}``.
    """
    path = find_session_file(claude_session_id, projects_root=projects_root)
    if path is None:
        return []
    events: list[dict] = []
    try:
        with path.open('r', encoding='utf-8') as fh:
            for raw_line in fh:
                event = _coerce_event(raw_line)
                if event is None:
                    continue
                events.append(event)
                if len(events) >= max_events:
                    break
    except OSError:
        return []
    return events


def _coerce_event(raw_line: str) -> dict | None:
    line = raw_line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    event_type = str(payload.get('type', '') or '')
    if event_type not in _RELEVANT_EVENT_TYPES:
        return None
    if event_type == 'user':
        message = payload.get('message')
        if _is_tool_result_only(message):
            return payload
        if not _has_displayable_text(message):
            return None
    return payload


_ORCHESTRATION_PROMPT_MARKERS = (
    'Security guardrails:',
    'Tool guardrails:',
    'Address pull request comment',
    'When you are done:',
)


def _is_orchestration_prompt(message) -> bool:
    """True when the user message is the orchestrator's auto-injected task prompt.

    Those carry security/tool guardrails plus an explicit completion
    contract — useful to Claude, noise to a human reading the history.
    """
    if not isinstance(message, dict):
        return False
    content = message.get('content')
    blocks = content if isinstance(content, list) else []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text = block.get('text') or ''
        if any(marker in text for marker in _ORCHESTRATION_PROMPT_MARKERS):
            return True
    return False


_RELEVANT_EVENT_TYPES = frozenset(
    {
        'user',
        'assistant',
        'system',
        'result',
    }
)


def _has_displayable_text(message) -> bool:
    if not isinstance(message, dict):
        return False
    content = message.get('content')
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get('type') == 'text' and (block.get('text') or '').strip():
            return True
    return False


def _is_tool_result_only(message) -> bool:
    if not isinstance(message, dict):
        return False
    content = message.get('content')
    if not isinstance(content, list) or not content:
        return False
    return all(
        isinstance(block, dict) and block.get('type') == 'tool_result'
        for block in content
    )


def iter_event_paths(
    *,
    projects_root: Path | str | None = None,
) -> Iterable[Path]:
    """Yield every JSONL transcript path on disk (debugging helper)."""
    root = Path(projects_root) if projects_root else _DEFAULT_PROJECTS_ROOT
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        for path in sorted(entry.glob('*.jsonl')):
            yield path


def resolve_claude_session_id(manager, workspace_manager, task_id: str) -> str:
    """Return the Claude session id bound to ``task_id``, or ``''``.

    Tries the live session manager's record first (its ``claude_session_id``
    field is set when kato spawned the agent in-process), then falls back
    to the workspace metadata's ``agent_session_id`` (generic field name)
    or ``claude_session_id`` (legacy pre-rename records). The fallback
    chain lets a freshly-booted webserver attach to an orphan workspace
    on disk even before the scan loop re-establishes the live record.

    Lives in ``claude_core_lib`` because the field name + the downstream
    consumer (Claude's JSONL transcript replay) are Claude-specific.
    Other backends (OpenHands, Codex) don't have an equivalent webserver
    SSE history-replay path, so they don't need an analogue.
    """
    if manager is not None:
        try:
            record = manager.get_record(task_id)
        except Exception:
            record = None
        if record is not None and getattr(record, 'claude_session_id', ''):
            return str(record.claude_session_id)
    if workspace_manager is not None:
        try:
            workspace = workspace_manager.get(task_id)
        except Exception:
            workspace = None
        if workspace is not None:
            agent_id = (
                getattr(workspace, 'agent_session_id', '')
                or getattr(workspace, 'claude_session_id', '')
                or ''
            )
            if agent_id:
                return str(agent_id)
    return ''
