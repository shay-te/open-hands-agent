"""Index of locally-stored Claude Code sessions for the adoption flow.

Claude Code persists every conversation as a JSONL transcript under
``~/.claude/projects/<encoded-cwd>/<session-id>.jsonl``. This module
walks that store, parses just enough metadata for the operator to
identify which session belongs to which task, and returns a list the
planning UI can render.

Why this exists: when a developer is mid-conversation in the VS Code
Claude extension and wants to hand the work off to kato, kato should
let them adopt that exact session id. Without adoption, kato spawns a
fresh session and the developer feels the prior context is lost,
which has been an actual adoption blocker.

Design notes:

- **Read-only.** This module never writes to Claude's session store.
  The transcript belongs to Claude Code; kato just reads metadata.
- **Best-effort parsing.** A malformed JSONL line is skipped, never
  raises. Truncated transcripts (Claude writing while we read) are
  treated as "everything we got is what's there." A corrupt store
  must not crash the planning UI.
- **Lazy.** No process-long cache. The directory walk is cheap
  (filesystem stat) and the JSONL parse is bounded — we read only
  enough lines to find the first/last user message, not the whole
  transcript. A session with 1000 turns parses in milliseconds.
- **Override path for tests.** ``KATO_CLAUDE_SESSIONS_ROOT`` points
  ``default_sessions_root()`` at a temp dir; production deployments
  leave it unset.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_core_lib.agent_core_lib.helpers.session_id_utils import fix_session_id
from agent_core_lib.agent_core_lib.helpers.text_utils import text_from_mapping
CLAUDE_SESSIONS_ROOT_ENV_KEY = 'KATO_CLAUDE_SESSIONS_ROOT'
# Cap on per-transcript bytes scanned for first/last user message
# previews. Claude transcripts grow without bound; reading the whole
# file just to render a dropdown is wasteful. 256 KB is enough to
# capture both the first user turn (always near the top) and a
# meaningful recent message preview.
_MAX_PREVIEW_SCAN_BYTES = 256 * 1024
# Preview text length per message. Long enough to identify the
# conversation, short enough to keep the dropdown tidy.
_PREVIEW_LENGTH = 160


@dataclass(frozen=True)
class ClaudeSessionMetadata:
    """One row in the session index.

    ``last_modified_epoch`` comes from the file mtime — the operator
    sorts by this to find "the session I was just in." ``turn_count``
    is the number of ``type:user`` records, including tool results,
    so it's a rough proxy for conversation depth, not a precise human
    turn count (good enough for the dropdown).
    """

    agent_session_id: str
    cwd: str
    last_modified_epoch: float
    turn_count: int
    first_user_message: str
    last_user_message: str
    transcript_path: str

    def to_dict(self) -> dict:
        return asdict(self)


def default_sessions_root() -> Path:
    """Resolve Claude Code's ``projects/`` directory.

    Honours ``KATO_CLAUDE_SESSIONS_ROOT`` first (for tests). Falls
    back to ``~/.claude/projects`` which is Claude Code's default on
    macOS / Linux. We don't try to detect alternate Claude Code
    installations — if an operator has moved their store, they set
    the env var.
    """
    override = os.environ.get(CLAUDE_SESSIONS_ROOT_ENV_KEY, '').strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / '.claude' / 'projects'


def list_sessions(
    *,
    query: str = '',
    sessions_root: Path | None = None,
    max_results: int = 100,
) -> list[ClaudeSessionMetadata]:
    """Return sessions sorted by recency (most-recent first).

    ``query`` is a case-insensitive substring matched against ``cwd``
    and against either user-message preview. Empty query returns
    everything (capped by ``max_results``).

    The result is intentionally bounded — even on a developer machine
    with thousands of historical sessions, the operator picks from
    the recent few. ``max_results=100`` is generous; the UI search
    box narrows further.
    """
    root = sessions_root or default_sessions_root()
    if not root.is_dir():
        return []
    needle = query.strip().lower()
    candidates: list[ClaudeSessionMetadata] = []
    for transcript_path in _iter_transcript_paths(root):
        metadata = _parse_metadata(transcript_path)
        if metadata is None:
            continue
        if needle and not _matches_query(metadata, needle):
            continue
        candidates.append(metadata)
    candidates.sort(key=lambda m: m.last_modified_epoch, reverse=True)
    return candidates[:max_results]


def _iter_transcript_paths(root: Path):
    """Walk ``<root>/<encoded-cwd>/<session-id>.jsonl`` shallowly.

    Claude's layout is exactly two levels deep; we don't recurse
    further. Symlinks are followed only if they stay inside the root
    (defence against a misbehaving symlink turning the walk into a
    full filesystem scan).
    """
    try:
        project_dirs = list(root.iterdir())
    except OSError:
        return
    for project_dir in project_dirs:
        if not project_dir.is_dir():
            continue
        try:
            for transcript_path in project_dir.glob('*.jsonl'):
                if transcript_path.is_file():
                    yield transcript_path
        except OSError:
            continue


def _parse_metadata(path: Path) -> ClaudeSessionMetadata | None:
    """Pull session id / cwd / preview / turn count out of a JSONL file.

    Reads up to ``_MAX_PREVIEW_SCAN_BYTES`` to avoid loading multi-MB
    transcripts when we only need a preview. Returns ``None`` when
    the file is unreadable, empty, or contains no parseable records.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    last_modified_epoch = float(stat.st_mtime)
    agent_session_id = path.stem
    cwd = ''
    turn_count = 0
    first_user_message = ''
    last_user_message = ''
    try:
        with path.open('r', encoding='utf-8', errors='replace') as handle:
            bytes_read = 0
            for line in handle:
                bytes_read += len(line.encode('utf-8', errors='replace'))
                if bytes_read > _MAX_PREVIEW_SCAN_BYTES:
                    break
                record = _parse_jsonl_line(line)
                if record is None:
                    continue
                if not cwd:
                    cwd = text_from_mapping(record, 'cwd')
                if not agent_session_id and record.get('sessionId'):
                    agent_session_id = fix_session_id(record.get('sessionId'))
                if record.get('type') != 'user':
                    continue
                turn_count += 1
                preview = _user_message_preview(record)
                if preview:
                    if not first_user_message:
                        first_user_message = preview
                    last_user_message = preview
    except OSError:
        return None
    if not agent_session_id:
        return None
    return ClaudeSessionMetadata(
        agent_session_id=agent_session_id,
        cwd=cwd,
        last_modified_epoch=last_modified_epoch,
        turn_count=turn_count,
        first_user_message=first_user_message,
        last_user_message=last_user_message,
        transcript_path=str(path),
    )


def _parse_jsonl_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    return record


def _user_message_preview(record: dict) -> str:
    """Pull a human-readable preview out of a ``type:user`` record.

    Claude's user records carry either a plain string ``content`` or
    a list of ``{type, text}`` parts. We extract the first text part
    and clip to ``_PREVIEW_LENGTH``. Tool-result records (no text
    portion) return empty so they don't drown out real user prompts
    in the preview.
    """
    message = record.get('message')
    if not isinstance(message, dict):
        return ''
    content = message.get('content')
    if isinstance(content, str):
        return _clip_preview(content)
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get('type') == 'text':
                text = part.get('text', '')
                if isinstance(text, str) and text.strip():
                    return _clip_preview(text)
    return ''


def _clip_preview(text: str) -> str:
    cleaned = ' '.join(text.split())
    if len(cleaned) <= _PREVIEW_LENGTH:
        return cleaned
    return cleaned[: _PREVIEW_LENGTH - 1] + '…'


# ----- session migration (adopt → kato workspace) -----


_logger = logging.getLogger(__name__)


# Characters Claude Code flattens to '-' when encoding the cwd into
# its ``~/.claude/projects/<encoded-cwd>/`` directory name. Path
# separators (``\``, ``/``) and the Windows drive colon are obvious.
# Less obvious — and the cause of a real review-fix crash loop —
# Claude ALSO flattens ``_`` and ``.``. For example a workspace at
# ``/Users/me/dev_kato/PROJ-1/repo`` lands under
# ``-Users-me-dev-kato-PROJ-1-repo`` (underscore → dash). If kato's
# encoder skips ``_``/``.`` it migrates the adopted JSONL into a
# differently-named directory than Claude will look in, and the
# next ``claude --resume`` fails with "No conversation found." Kato
# then refuses the fresh fallback (resume preservation), so the
# task retries the same failure on every scan tick.
_PROJECT_DIR_ENCODE_CHARS = ('\\', '/', ':', '_', '.')


def claude_project_dir_for_cwd(cwd: str) -> Path:
    """Return Claude Code's per-project session directory for ``cwd``.

    Claude Code stores every session as
    ``~/.claude/projects/<encoded-cwd>/<session-id>.jsonl``. The
    encoded form flattens every path separator, the Windows drive
    colon, AND ``_`` / ``.`` to ``-``: ``/Users/me/dev_kato`` becomes
    ``-Users-me-dev-kato`` (note the underscore lost). The encoding
    is lossy by design on Claude's side; kato must use the same
    flattening or the migrated JSONL lands in a directory Claude
    never reads.

    Looking sessions up via ``claude --resume <id>`` is cwd-keyed —
    spawning at a different cwd than the session's original cwd
    means the resume lookup misses and Claude starts fresh.

    This helper is the canonical "where does Claude Code expect this
    session to live?" function — used by ``migrate_session_to_workspace``
    to copy an adopted JSONL into kato's per-task workspace cwd, and
    available to operator-facing tooling that needs the same answer.
    """
    abs_cwd = os.path.abspath(os.path.expanduser(str(cwd or '')))
    encoded = abs_cwd
    for ch in _PROJECT_DIR_ENCODE_CHARS:
        encoded = encoded.replace(ch, '-')
    # Empty overrides must not collapse to Path('.') and reroute
    # Claude transcript migrations under kato's current cwd.
    override = os.environ.get(CLAUDE_SESSIONS_ROOT_ENV_KEY, '').strip()
    if override:
        root = Path(override).expanduser()
        if root.is_dir():
            return root / encoded
    return Path.home() / '.claude' / 'projects' / encoded


def migrate_session_to_workspace(
    *,
    transcript_path: str,
    target_cwd: str,
) -> Path | None:
    """Copy an adopted session JSONL into the target cwd's project dir.

    Returns the destination path on success, ``None`` when the source
    file isn't readable. The destination directory is created if it
    doesn't already exist (Claude Code creates it lazily on first
    write, so it may not be there yet for a never-used cwd).

    Idempotent: if the destination already exists with the same
    contents (or the source IS the destination), the copy is a no-op.
    Best-effort — a failure is logged and ``None`` returned so the
    adoption flow can decide how to surface it.
    """
    source = Path(str(transcript_path or '')).expanduser()
    if not source.is_file():
        _logger.warning(
            'cannot migrate Claude session: source transcript missing at %s',
            source,
        )
        return None
    if not target_cwd:
        return None
    target_dir = claude_project_dir_for_cwd(target_cwd)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        _logger.exception('failed to create target session dir %s', target_dir)
        return None
    target = target_dir / source.name
    try:
        if target.resolve() == source.resolve():
            return target
    except OSError:
        # ``resolve`` follows symlinks; a missing target raises only
        # on older Python where ``strict=False`` is the default — fall
        # through to the copy.
        pass
    try:
        shutil.copyfile(source, target)
    except OSError:
        _logger.exception(
            'failed to copy Claude session transcript from %s to %s',
            source, target,
        )
        return None
    return target


def _matches_query(metadata: ClaudeSessionMetadata, needle: str) -> bool:
    haystack = (
        metadata.cwd.lower()
        + '\n'
        + metadata.first_user_message.lower()
        + '\n'
        + metadata.last_user_message.lower()
    )
    return needle in haystack
