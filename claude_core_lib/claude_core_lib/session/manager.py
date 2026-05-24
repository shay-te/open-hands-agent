"""Registry of live Claude planning sessions, one per Kato task.

Owns the lifecycle of :class:`StreamingClaudeSession` instances:

* Creates a session when the orchestrator (or webserver) declares a task is
  ready for planning.
* Persists session metadata (task id, claude session id, status, timestamps)
  to disk so a kato restart can rehydrate tabs in the planning UI.
* Tears sessions down when the ticket leaves a "live" state or when the
  process is shutting down.

Pure infrastructure — no Flask, no agent_service. The orchestrator and
the webserver both talk to this manager; the manager talks to the
streaming subprocess.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agent_core_lib.agent_core_lib.helpers.atomic_write import atomic_write_json
from agent_core_lib.agent_core_lib.helpers.logging_utils import configure_logger
from agent_core_lib.agent_core_lib.helpers.session_id_utils import (
    AGENT_SESSION_ID,
    fix_session_id,
    has_session_id,
    read_session_id_from,
    read_session_id_from_mapping,
    same_session_id,
)
from agent_core_lib.agent_core_lib.helpers.text_utils import (
    normalized_text,
    text_from_mapping,
)
from claude_core_lib.claude_core_lib.session.streaming import StreamingClaudeSession


SESSION_STATUS_ACTIVE = 'active'
SESSION_STATUS_DONE = 'done'
SESSION_STATUS_REVIEW = 'review'
SESSION_STATUS_TERMINATED = 'terminated'

SUPPORTED_SESSION_STATUSES = frozenset(
    {
        SESSION_STATUS_ACTIVE,
        SESSION_STATUS_DONE,
        SESSION_STATUS_REVIEW,
        SESSION_STATUS_TERMINATED,
    }
)


@dataclass
class PlanningSessionRecord(object):
    """On-disk metadata for one planning session.

    Stored as JSON at ``<state_dir>/<task_id>.json``. The live subprocess is
    NOT part of this record — only what's needed to rehydrate / display the
    tab after a restart. The actual conversation transcript lives inside
    Claude Code's own session storage and is rejoined via ``claude --resume``.
    """

    task_id: str
    task_summary: str = ''
    # The agent's session id for this task. ``agent_session_id`` is
    # the canonical name across every kato agent backend (Claude,
    # Codex, OpenHands, ...).
    agent_session_id: str = ''
    status: str = SESSION_STATUS_ACTIVE
    created_at_epoch: float = field(default_factory=time.time)
    updated_at_epoch: float = field(default_factory=time.time)
    cwd: str = ''
    # The branch kato prepared for this task. The webserver compares this
    # against the repo's HEAD before forwarding any message to the live
    # subprocess; if they diverge (kato has moved on to a different task)
    # the send is rejected. Empty string disables the check (wait-planning
    # tabs that aren't owned by the orchestrator).
    expected_branch: str = ''
    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> 'PlanningSessionRecord':
        return cls(
            task_id=text_from_mapping(payload, 'task_id'),
            task_summary=str(payload.get('task_summary', '') or ''),
            agent_session_id=read_session_id_from_mapping(payload),
            status=str(payload.get('status', SESSION_STATUS_ACTIVE) or SESSION_STATUS_ACTIVE),
            created_at_epoch=float(payload.get('created_at_epoch', time.time()) or time.time()),
            updated_at_epoch=float(payload.get('updated_at_epoch', time.time()) or time.time()),
            cwd=text_from_mapping(payload, 'cwd'),
            expected_branch=str(payload.get('expected_branch', '') or ''),
        )


class ClaudeSessionManager(object):
    """Owns every active streaming Claude session for the running Kato.

    Thread-safe by design: the orchestrator may register / terminate sessions
    while the webserver simultaneously reads them.
    """

    DEFAULT_STATE_DIR_NAME = '.kato/sessions'

    @classmethod
    def from_config(
        cls,
        open_cfg,
        agent_backend: str,
    ) -> 'ClaudeSessionManager | None':
        """Build the manager (or return None) from the kato config block.

        Only the Claude backend exposes live in-process sessions for the UI
        to talk to; everything else returns None and the planning webserver
        gracefully shows an empty tab list.
        """
        if str(agent_backend or '').strip().lower() != 'claude':
            return None
        state_dir = (
            os.environ.get('KATO_SESSION_STATE_DIR', '').strip()
            or str(Path.home() / cls.DEFAULT_STATE_DIR_NAME)
        )
        return cls(state_dir=state_dir)

    def __init__(
        self,
        *,
        state_dir: str | os.PathLike[str],
        session_factory=None,
    ) -> None:
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._session_factory = session_factory or StreamingClaudeSession
        self._lock = threading.RLock()
        # Per-task spawn locks. Held during the (slow) Claude subprocess
        # spawn so two concurrent spawns for the SAME task serialize, while
        # spawns for DIFFERENT tasks run in parallel. The global ``_lock``
        # protects the registry mutations only — never held across the
        # spawn itself, which would serialize all parallel-runner workers.
        self._spawn_locks: dict[str, threading.Lock] = {}
        self._sessions: dict[str, StreamingClaudeSession] = {}
        self._records: dict[str, PlanningSessionRecord] = {}
        self._workspace_manager = None
        # Default ``done_callback`` injected into every spawned session.
        # ``AgentService`` sets this via ``set_done_callback`` so Claude
        # printing ``<KATO_TASK_DONE>`` triggers the publish flow.
        self._done_callback = None
        self.logger = configure_logger(self.__class__.__name__)
        self._load_persisted_records()

    def set_done_callback(self, callback) -> None:
        """Register the function to fire when a session detects ``<KATO_TASK_DONE>``.

        Called once during kato startup wiring with
        ``AgentService.finish_task_planning_session``. Every session
        spawned after this picks up the callback automatically.
        """
        self._done_callback = callback

    def attach_workspace_manager(self, workspace_manager) -> None:
        """Mirror session_id + cwd into workspace metadata as we capture them.

        Optional wiring: when the orchestrator boots both managers it calls
        this so kato has a single source of truth for "which Claude session
        belongs to this task" living next to the workspace folder.
        """
        self._workspace_manager = workspace_manager
        self._seed_records_from_workspaces()

    def _seed_records_from_workspaces(self) -> None:
        """Recover Claude session ids from workspace metadata on boot.

        If kato's own state dir was wiped (or is on a different host than
        the previous run), the per-task PlanningSessionRecord is missing
        but the workspace folder still has ``.kato-meta.json`` with the
        Claude session id. Fold those into the in-memory records so the
        next spawn can ``--resume`` cleanly.
        """
        if self._workspace_manager is None:
            return
        try:
            workspace_records = self._workspace_manager.list_workspaces()
        except Exception:
            self.logger.exception('failed to list workspaces during session seed')
            return
        with self._lock:
            for workspace in workspace_records:
                # ``agent_session_id`` is workspace_core_lib's generic name
                # for the bound agent session id.
                session_id = read_session_id_from(workspace)
                if not session_id:
                    continue
                lookup_key = self._lookup_key(workspace.task_id)
                existing = self._records.get(lookup_key)
                existing_id = read_session_id_from(existing)
                if existing is not None and existing_id:
                    continue
                record = existing or PlanningSessionRecord(
                    task_id=workspace.task_id,
                    task_summary=str(getattr(workspace, 'task_summary', '') or ''),
                    status=SESSION_STATUS_TERMINATED,
                )
                record.agent_session_id = session_id
                cwd = str(getattr(workspace, 'cwd', '') or '').strip()
                if cwd and not record.cwd:
                    record.cwd = cwd
                record.updated_at_epoch = time.time()
                self._records[lookup_key] = record
                self._persist_record(record)

    # ----- public API -----

    def start_session(
        self,
        *,
        task_id: str,
        task_summary: str = '',
        initial_prompt: str = '',
        binary: str = '',
        cwd: str = '',
        model: str = '',
        permission_mode: str = '',
        permission_prompt_tool: str = '',
        allowed_tools: str = '',
        disallowed_tools: str = '',
        max_turns: int | None = None,
        effort: str = '',
        env: dict[str, str] | None = None,
        expected_branch: str = '',
        architecture_doc_path: str = '',
        lessons_path: str = '',
        docker_mode_on: bool = False,
        additional_dirs: list[str] | None = None,
    ) -> StreamingClaudeSession:
        """Spawn (or rehydrate) the streaming session bound to ``task_id``.

        If a previous run wrote a record for this task, the new subprocess
        resumes the same Claude session id so the planning conversation
        picks up where it left off.
        """
        normalized_task_id = self._normalize_task_id(task_id)
        lookup_key = self._lookup_key(task_id)
        factory_kwargs = {
            'task_id': normalized_task_id,
            'binary': binary,
            'cwd': cwd,
            'model': model,
            'permission_mode': permission_mode,
            'permission_prompt_tool': permission_prompt_tool,
            'allowed_tools': allowed_tools,
            'disallowed_tools': disallowed_tools,
            'max_turns': max_turns,
            'effort': effort,
            'env': env,
            'architecture_doc_path': architecture_doc_path,
            'lessons_path': lessons_path,
            'docker_mode_on': docker_mode_on,
            'additional_dirs': list(additional_dirs or []),
            'done_callback': self._done_callback,
        }
        # Per-task spawn lock: get-or-create under the global lock, then
        # hold the per-task lock (NOT the global lock) for the actual
        # spawn. This is what lets parallel-runner workers spawn
        # different-task sessions concurrently — earlier the global lock
        # was held across the spawn and serialised everything.
        with self._lock:
            spawn_lock = self._spawn_locks.setdefault(
                lookup_key, threading.Lock(),
            )
        with spawn_lock:
            with self._lock:
                existing = self._sessions.get(lookup_key)
                if existing is not None and existing.is_alive:
                    drifted = self._discard_if_session_id_drifted_locked(
                        lookup_key, normalized_task_id, existing,
                    )
                    if not drifted:
                        return existing
                    existing = None
                previous_record = self._records.get(lookup_key)
                resume_session_id = self._resume_id_for_spawn(
                    normalized_task_id,
                    previous_record,
                    existing,
                )
            # Warn on huge transcripts, but never trade away the user's
            # session id. A slow resume is better than silent session drift.
            resume_session_id = self._gate_resume_by_jsonl_size(
                normalized_task_id, resume_session_id,
            )
            # One-session-per-task invariant: if the task already has a
            # session id on file, ensure the JSONL transcript is present
            # at the spawn cwd's project dir before passing ``--resume``.
            # ``claude --resume <id>`` is cwd-keyed — it looks at
            # ``~/.claude/projects/<encoded-cwd>/<id>.jsonl`` only — so
            # when kato switches cwds across spawns (workspace clone vs
            # source repo, sibling repos in a multi-repo task) the
            # resume previously failed with "No conversation found" and
            # stale-resume handling blanked the id, ending the conversation. The
            # JSONL itself is a plain file: we copy it to the new cwd's
            # project dir and Claude resumes natively. Free in tokens
            # and idempotent.
            self._ensure_resume_jsonl_at_target_cwd(
                resume_session_id=resume_session_id,
                target_cwd=cwd,
            )
            # Spawn happens with NO global lock held — concurrent spawns
            # for different task ids run in parallel.
            session = self._spawn_with_resume_self_heal(
                normalized_task_id=normalized_task_id,
                factory_kwargs=factory_kwargs,
                initial_prompt=initial_prompt,
                resume_session_id=resume_session_id,
            )
            # Capture a first-spawn id, but never let a live process
            # replace an already-pinned operator session id.
            correction_expected_id = fix_session_id(session.agent_session_id)
            correction_can_replace = not bool(resume_session_id)
            session._session_id_correction_callback = (
                lambda sid, k=lookup_key, t=normalized_task_id,
                expected=correction_expected_id,
                can_replace=correction_can_replace: (
                    self._correct_session_id_in_record(
                        k, t, sid,
                        expected_existing_id=expected,
                        can_replace_existing=can_replace,
                    )
                )
            )
            with self._lock:
                self._sessions[lookup_key] = session
                self._record_session_metadata(
                    normalized_task_id=normalized_task_id,
                    session=session,
                    previous_record=previous_record,
                    task_summary=task_summary,
                    expected_branch=expected_branch,
                    resume_session_id=resume_session_id,
                )
            return session

    def _ensure_resume_jsonl_at_target_cwd(
        self,
        *,
        resume_session_id: str,
        target_cwd: str,
    ) -> None:
        """Copy the resume JSONL into ``target_cwd``'s project dir if needed.

        Defends the one-session-per-task invariant against cwd drift.
        ``claude --resume`` only finds a transcript under the spawn
        cwd's encoded project dir; kato's cwd legitimately changes
        across operations on the same task (review-fix in a sibling
        repo, retargeted workspace clone, etc.), so we copy the JSONL
        to wherever Claude will look for it. No-op when there's no
        resume id, no target cwd, no source transcript on disk, or
        the source already lives at the target.
        """
        if not resume_session_id or not target_cwd:
            return
        try:
            from claude_core_lib.claude_core_lib.session.history import (
                find_session_file,
            )
            from claude_core_lib.claude_core_lib.session.index import (
                claude_project_dir_for_cwd,
                migrate_session_to_workspace,
            )
        except ImportError:
            return
        try:
            source = find_session_file(resume_session_id)
        except Exception:
            self.logger.exception(
                'failed to locate resume transcript for session %s',
                resume_session_id,
            )
            return
        if source is None:
            return
        try:
            target_dir = claude_project_dir_for_cwd(target_cwd)
            if source.parent.resolve() == target_dir.resolve():
                return
        except OSError:
            pass
        # The source JSONL is left in place as a historical snapshot
        # of the conversation at the moment the cwd switched. Kato's
        # "one session per task" invariant lives at the session-id
        # level — kato's record points at exactly one id, and Claude
        # writes new turns only to the canonical copy at the spawn
        # cwd's project dir. The old file is harmless and useful for
        # forensics; orphan cleanup, if ever wanted, is a separate
        # housekeeping concern.
        try:
            copied = migrate_session_to_workspace(
                transcript_path=str(source),
                target_cwd=target_cwd,
            )
        except Exception:
            self.logger.exception(
                'failed to copy resume transcript for session %s into %s '
                '(--resume will likely fail; fresh-session drift is refused)',
                resume_session_id,
                target_cwd,
            )
            return
        if copied is None:
            # Copy quietly failed (best-effort path inside
            # migrate_session_to_workspace). Log at warning so the
            # next failed --resume is traceable.
            self.logger.warning(
                'task transcript migration returned None; resume id %s '
                'expected at %s — Claude will likely reject --resume '
                'and Kato will refuse a fresh fallback',
                resume_session_id,
                target_dir,
            )
            return
        # Verify the file landed where Claude will look for it.
        # Without this guard, an unexpected filesystem outcome (race,
        # symlink, permission anomaly) would still fall through to
        # the spawn and waste 4-5s before refusing the fresh fallback.
        if not Path(copied).is_file():
            self.logger.warning(
                'migrated JSONL not present at %s after copy; --resume '
                'will reject and Kato will refuse a fresh fallback',
                copied,
            )

    # Sessions whose JSONL transcript exceeds this byte count are NOT
    # resumed — the full history would exceed (or strain) the model's
    # context window, causing 10–15 minute startup delays before the
    # first token appears.  1 MB of JSONL ≈ 50–100 K tokens of real
    # content; well within Claude Opus's 200 K limit and loads in
    # under 30 s.  Above 1 MB the latency climbs sharply.
    _RESUME_JSONL_SIZE_LIMIT_BYTES: int = 1_000_000  # 1 MB

    def _gate_resume_by_jsonl_size(
        self,
        normalized_task_id: str,
        resume_session_id: str,
    ) -> str:
        """Warn when a transcript is huge, but always keep the resume id."""
        if not resume_session_id:
            return resume_session_id
        try:
            from claude_core_lib.claude_core_lib.session.history import find_session_file
            path = find_session_file(resume_session_id)
        except Exception:
            return resume_session_id
        if path is None:
            return resume_session_id
        try:
            size = path.stat().st_size
        except OSError:
            return resume_session_id
        if size <= self._RESUME_JSONL_SIZE_LIMIT_BYTES:
            return resume_session_id
        self.logger.warning(
            'task %s: session JSONL is %.0f KB (limit %d KB); '
            'resume may be slow, but keeping --resume to preserve the '
            'operator session id',
            normalized_task_id,
            size / 1024,
            self._RESUME_JSONL_SIZE_LIMIT_BYTES // 1024,
        )
        return resume_session_id

    def _resume_id_for_spawn(
        self,
        normalized_task_id: str,
        previous_record: PlanningSessionRecord | None,
        existing_session,
    ) -> str:
        """Return the resume id to pass to the next spawn (or '' for fresh).

        Even when a previous live process rejected the id, keep returning
        it. Kato must fail loud rather than silently drift to a fresh
        Claude session id.
        """
        raw_resume_id = previous_record.agent_session_id if previous_record else ''
        resume_session_id = fix_session_id(raw_resume_id)
        if previous_record is not None and raw_resume_id != resume_session_id:
            previous_record.agent_session_id = resume_session_id
            self._persist_record(previous_record)
        if not resume_session_id or existing_session is None:
            return resume_session_id
        if not self._died_with_stale_resume_id(existing_session, resume_session_id):
            return resume_session_id
        self.logger.warning(
            'task %s: claude rejected resume id %s; keeping it pinned '
            'and retrying because session id preservation is required',
            normalized_task_id,
            resume_session_id,
        )
        return resume_session_id

    def _spawn_with_resume_self_heal(
        self,
        *,
        normalized_task_id: str,
        factory_kwargs: dict,
        initial_prompt: str,
        resume_session_id: str,
    ) -> StreamingClaudeSession:
        """Spawn the subprocess, refusing to drift when a resume id rejects."""
        # Diagnostic: log where Claude will look for the JSONL so a
        # future "resume silently spawned fresh" report has the path
        # information without needing to attach a debugger.
        if resume_session_id:
            self._log_resume_jsonl_state(
                normalized_task_id=normalized_task_id,
                resume_session_id=resume_session_id,
                target_cwd=factory_kwargs.get('cwd', ''),
            )
        session = self._session_factory(
            resume_session_id=resume_session_id, **factory_kwargs,
        )
        session.start(initial_prompt=initial_prompt)
        if not resume_session_id:
            return session
        if not self._wait_for_stale_resume_failure(session, resume_session_id):
            return session
        self.logger.warning(
            'task %s: claude rejected resume id %s on first spawn; '
            'refusing to start a fresh session because session id '
            'preservation is required',
            normalized_task_id,
            resume_session_id,
        )
        try:
            session.terminate()
        except Exception:
            pass
        raise RuntimeError(
            f'Claude rejected resume id {resume_session_id} for task '
            f'{normalized_task_id}; refusing to start a fresh session.'
        )

    def _log_resume_jsonl_state(
        self,
        *,
        normalized_task_id: str,
        resume_session_id: str,
        target_cwd: str,
    ) -> None:
        """Emit pre-spawn diagnostics for ``--resume`` so future failures are debuggable.

        Reports (1) where the JSONL transcript actually lives on
        disk (via the cwd-agnostic glob lookup) and (2) where the
        spawn's cwd would make Claude look for it. A mismatch means
        ``--resume`` will fail unless
        ``_ensure_resume_jsonl_at_target_cwd`` copies the JSONL.
        """
        try:
            from claude_core_lib.claude_core_lib.session.history import (
                find_session_file,
            )
            from claude_core_lib.claude_core_lib.session.index import (
                claude_project_dir_for_cwd,
            )
        except ImportError:
            return
        try:
            source = find_session_file(resume_session_id)
        except Exception:
            self.logger.exception(
                'task %s: failed to locate JSONL for resume id %s',
                normalized_task_id, resume_session_id,
            )
            return
        try:
            target_dir = claude_project_dir_for_cwd(target_cwd) if target_cwd else None
        except Exception:
            target_dir = None
        source_dir = str(source.parent) if source is not None else '(not found)'
        target_text = str(target_dir) if target_dir is not None else '(no cwd)'
        matches = (
            target_dir is not None
            and source is not None
            and source.parent.resolve() == target_dir.resolve()
        )
        self.logger.info(
            'task %s: --resume %s; JSONL at %s; spawn cwd dir %s; aligned=%s',
            normalized_task_id,
            resume_session_id,
            source_dir,
            target_text,
            matches,
        )

    def _correct_session_id_in_record(
        self, lookup_key: str, task_id: str, actual_id: str,
        *,
        expected_existing_id: str = '',
        can_replace_existing: bool = False,
    ) -> None:
        """Update the persisted record when Claude reports a different session id.

        Called from the session's ``_session_id_correction_callback`` (fired
        from the session reader thread when the init event arrives).  Thread-safe
        via ``_lock``.  Updates both the in-memory record and its on-disk
        counterpart so the next ``start_session`` for this task resumes from
        Claude's actual JSONL rather than kato's expected UUID.
        """
        actual_id = fix_session_id(actual_id)
        if not has_session_id(actual_id):
            return
        with self._lock:
            record = self._records.get(lookup_key)
            if record is None:
                return
            record_id = fix_session_id(record.agent_session_id)
            if same_session_id(record_id, actual_id):
                if record.agent_session_id != actual_id:
                    record.agent_session_id = actual_id
                    record.updated_at_epoch = time.time()
                    self._persist_record(record)
                return
            if record_id:
                can_replace = (
                    can_replace_existing
                    and same_session_id(expected_existing_id, record_id)
                )
                if not can_replace:
                    self.logger.warning(
                        'task %s: live Claude reported session id %s, but '
                        'record is pinned to %s; keeping the persisted id',
                        task_id, actual_id, record_id,
                    )
                    return
                self.logger.warning(
                    'task %s: fresh spawn reported actual session id %s '
                    'instead of requested %s; recording actual id',
                    task_id, actual_id, record_id,
                )
            else:
                self.logger.info(
                    'task %s: recording live agent_session_id %s',
                    task_id, actual_id,
                )
            record.agent_session_id = actual_id
            record.updated_at_epoch = time.time()
            self._persist_record(record)

    def _record_session_metadata(
        self,
        *,
        normalized_task_id: str,
        session: StreamingClaudeSession,
        previous_record: PlanningSessionRecord | None,
        task_summary: str,
        expected_branch: str,
        resume_session_id: str,
    ) -> None:
        """Build and persist the on-disk record for the just-spawned session."""
        active_id = (
            fix_session_id(resume_session_id)
            or read_session_id_from(session)
        )
        record = PlanningSessionRecord(
            task_id=normalized_task_id,
            task_summary=normalized_text(task_summary)
            or (previous_record.task_summary if previous_record else ''),
            agent_session_id=active_id,
            status=SESSION_STATUS_ACTIVE,
            created_at_epoch=(
                previous_record.created_at_epoch
                if previous_record
                else time.time()
            ),
            updated_at_epoch=time.time(),
            cwd=session.cwd,
            # Always use the caller's value — wait-planning explicitly
            # passes '' (no lock), and the autonomous runner always passes
            # a real branch. Falling back to the persisted value would
            # silently re-arm a stale lock from a prior buggy run.
            expected_branch=normalized_text(expected_branch),
        )
        self._records[self._lookup_key(normalized_task_id)] = record
        self._persist_record(record)

    def get_session(self, task_id: str) -> StreamingClaudeSession | None:
        with self._lock:
            lookup_key = self._lookup_key(task_id)
            session = self._sessions.get(lookup_key)
            if session is None:
                return None
            if getattr(session, 'is_alive', False):
                drifted = self._discard_if_session_id_drifted_locked(
                    lookup_key, self._normalize_task_id(task_id), session,
                )
                if drifted:
                    return None
            return session

    def get_record(self, task_id: str) -> PlanningSessionRecord | None:
        with self._lock:
            record = self._records.get(self._lookup_key(task_id))
            return self._with_refreshed_session_id(record)

    def list_records(self) -> list[PlanningSessionRecord]:
        with self._lock:
            return [
                self._with_refreshed_session_id(record)
                for record in self._records.values()
            ]

    def adopt_session_id(
        self,
        task_id: str,
        *,
        agent_session_id: str,
        task_summary: str = '',
    ) -> PlanningSessionRecord:
        """Bind ``agent_session_id`` to ``task_id`` so the next spawn resumes it.

        Used by the planning UI when an operator picks an existing
        Claude Code session (e.g. a VS Code extension chat) to hand
        off to kato. The next ``start_session`` for ``task_id`` will
        ``--resume <agent_session_id>`` instead of starting a fresh
        conversation.

        Adoption does NOT change the spawn cwd — kato continues to
        run Claude at its per-task workspace clone, with a SNAPSHOT
        copy of the source JSONL placed under that clone's projects
        dir. This keeps kato edits isolated from the operator's live
        VS Code checkout (a hard-won property: the operator wants
        kato's git state separate from their working copy). The
        snapshot does mean the resumed conversation diverges from
        the source instance the moment either side takes another
        turn — see ``docs/adopting-existing-claude-sessions.md`` for
        the full lifecycle.

        The adopted id is mirrored to the workspace metadata so it
        survives a kato restart, and persisted to the per-task record
        so an in-process reader sees it immediately. If a live session
        is already running for ``task_id`` the caller is expected to
        terminate it first — adoption doesn't tear down a running
        subprocess on its own (that would be a confusing implicit
        side-effect).
        """
        new_id = fix_session_id(agent_session_id)
        if not new_id:
            raise ValueError('agent_session_id must be non-empty')
        normalized_task_id = self._normalize_task_id(task_id)
        lookup_key = self._lookup_key(task_id)
        with self._lock:
            spawn_lock = self._spawn_locks.setdefault(
                lookup_key, threading.Lock(),
            )
        with spawn_lock:
            now = time.time()
            with self._lock:
                # Share the start_session lock so adoption cannot slip
                # between spawn and metadata persistence for this task.
                existing_session = self._sessions.get(lookup_key)
                if existing_session is not None and getattr(
                    existing_session, 'is_alive', False,
                ):
                    raise RuntimeError(
                        f'cannot adopt session id for task {normalized_task_id}: '
                        f'a live Claude subprocess is still running for this '
                        f'task. Terminate the live session first '
                        f'(``terminate_session(task_id)``) before adopting; '
                        f'otherwise the next message would silently reuse the '
                        f'running subprocess instead of resuming the adopted id.'
                    )
                record = self._records.get(lookup_key)
                if record is None:
                    record = PlanningSessionRecord(
                        task_id=normalized_task_id,
                        task_summary=str(task_summary or ''),
                        status=SESSION_STATUS_TERMINATED,
                    )
                    self._records[lookup_key] = record
                existing_id = fix_session_id(record.agent_session_id)
                if existing_id and existing_id != new_id:
                    raise RuntimeError(
                        f'cannot adopt session id {new_id} for task '
                        f'{normalized_task_id}: existing session id '
                        f'{existing_id} is already pinned'
                    )
                record.agent_session_id = new_id
                if task_summary and not record.task_summary:
                    record.task_summary = str(task_summary)
                record.updated_at_epoch = now
                self._persist_record(record)
                self._mirror_to_workspace_metadata(record)
                return record

    def update_status(self, task_id: str, status: str) -> None:
        if status not in SUPPORTED_SESSION_STATUSES:
            raise ValueError(
                f'unknown session status: {status!r}; '
                f'supported: {sorted(SUPPORTED_SESSION_STATUSES)}'
            )
        normalized_task_id = self._normalize_task_id(task_id)
        lookup_key = self._lookup_key(task_id)
        with self._lock:
            record = self._records.get(lookup_key)
            if record is None:
                return
            record.status = status
            record.updated_at_epoch = time.time()
            self._persist_record(record)

    def terminate_session(self, task_id: str, *, remove_record: bool = False) -> None:
        normalized_task_id = self._normalize_task_id(task_id)
        lookup_key = self._lookup_key(task_id)
        with self._lock:
            session = self._sessions.pop(lookup_key, None)
            if session is not None:
                try:
                    session.terminate()
                except Exception:
                    self.logger.exception(
                        'failed to terminate streaming session for task %s',
                        normalized_task_id,
                    )
            if remove_record:
                # Capture the record BEFORE dropping it — we need its
                # Claude session id to delete the CLI transcript.
                removed = self._records.pop(lookup_key, None)
                self._delete_persisted_record(normalized_task_id)
                self._forget_claude_transcript(removed, normalized_task_id)
            else:
                record = self._records.get(lookup_key)
                if record is not None:
                    record.status = SESSION_STATUS_TERMINATED
                    record.updated_at_epoch = time.time()
                    self._persist_record(record)

    def shutdown(self) -> None:
        """Terminate every live session. Safe to call multiple times."""
        with self._lock:
            task_ids = list(self._sessions.keys())
        for task_id in task_ids:
            self.terminate_session(task_id)

    # ----- internals -----

    @classmethod
    def _wait_for_stale_resume_failure(
        cls,
        session,
        resume_session_id: str,
        *,
        max_wait_seconds: float = 4.0,
        poll_interval_seconds: float = 0.1,
    ) -> bool:
        """Poll briefly for Claude to reject the resume id and return True if it did.

        Claude exits within a second or two when ``--resume`` references
        a missing session, so a short wait here is enough to catch the
        common case without delaying healthy spawns. Returns False on
        timeout and lets the normal session path continue.
        """
        deadline = time.monotonic() + max(0.0, float(max_wait_seconds))
        while time.monotonic() < deadline:
            if not session.is_alive:
                return cls._died_with_stale_resume_id(session, resume_session_id)
            if cls._died_with_stale_resume_id(session, resume_session_id):
                return True
            time.sleep(poll_interval_seconds)
        return False

    @staticmethod
    def _died_with_stale_resume_id(session, resume_session_id: str) -> bool:
        """Did ``session`` exit because Claude couldn't find the resume id?

        We detect this from the captured stderr (where the CLI prints
        ``No conversation found with session ID: ...``) and from the
        terminal result text. The check is conservative because a false
        positive blocks a spawn that might have been healthy.

        The function REQUIRES the subprocess to have actually exited.
        An alive subprocess whose stderr happens to contain the marker
        text (e.g., a log line from Claude or a tool that echoes the
        session id for diagnostics) MUST NOT trigger stale-resume handling.
        """
        # Only an exited subprocess can be "died with stale resume id".
        # A still-alive session that happens to surface the marker in
        # stderr (e.g., a tool output) is NOT the failure mode we're
        # detecting.
        if bool(getattr(session, 'is_alive', False)):
            return False
        marker = f'No conversation found with session ID: {resume_session_id}'
        try:
            stderr_lines = session.stderr_snapshot()
        except Exception:
            stderr_lines = []
        for line in stderr_lines:
            if marker in line:
                return True
        terminal = getattr(session, 'terminal_event', None)
        if terminal is None:
            return False
        raw = getattr(terminal, 'raw', {}) or {}
        if not bool(raw.get('is_error', False)):
            return False
        result_text = str(raw.get('result', '') or '')
        return marker in result_text

    @staticmethod
    def _normalize_task_id(task_id: str) -> str:
        # Strip whitespace, PRESERVE original case. This value is what
        # gets stored on ``record.task_id`` so error messages, audit
        # logs, and the on-disk record's display field match what the
        # ticket system uses (e.g. ``PROJ-1``).
        normalized = str(task_id or '').strip()
        if not normalized:
            raise ValueError('task_id is required')
        return normalized

    @staticmethod
    def _lookup_key(task_id: str) -> str:
        # Canonical key for in-memory dicts (``_records``, ``_sessions``,
        # ``_spawn_locks``) AND for the on-disk filename. Lowercased so
        # ``PROJ-1`` and ``proj-1`` resolve to the same logical task.
        # Without this, two casings produce two records on Linux
        # (case-sensitive FS) and silent overwrite on macOS
        # (case-insensitive FS).
        return ClaudeSessionManager._normalize_task_id(task_id).lower()

    def _record_path(self, task_id: str) -> Path:
        # task ids in YouTrack/Jira/etc. tend to be filename-safe (e.g.
        # PROJ-123). We still strip any path separators just in case,
        # and lowercase via _lookup_key so different casings of the
        # same logical task share one file on disk.
        safe_name = self._lookup_key(task_id).replace('/', '_').replace(os.sep, '_')
        return self._state_dir / f'{safe_name}.json'

    def _persist_record(self, record: PlanningSessionRecord) -> None:
        atomic_write_json(
            self._record_path(record.task_id),
            record.to_dict(),
            logger=self.logger,
            label=f'planning session record for task {record.task_id}',
        )
        self._mirror_to_workspace_metadata(record)

    def _mirror_to_workspace_metadata(self, record: PlanningSessionRecord) -> None:
        if self._workspace_manager is None:
            return
        if not has_session_id(record.agent_session_id) and not record.cwd:
            return
        try:
            self._workspace_manager.update_agent_session(
                record.task_id,
                agent_session_id=fix_session_id(record.agent_session_id),
                cwd=record.cwd,
            )
        except Exception:
            self.logger.exception(
                'failed to mirror claude session id to workspace metadata for task %s',
                record.task_id,
            )

    def _forget_claude_transcript(self, record, task_id: str) -> None:
        """Delete the Claude CLI transcript for a forgotten task.

        Called only on the ``remove_record=True`` path (task done /
        closed / operator forget). The workspace clones + kato
        session record are already gone by here; the Claude
        transcript under ``~/.claude/projects/`` would otherwise
        accumulate forever. Best-effort — a unlink failure must not
        break ``terminate_session``.
        """
        agent_session_id = read_session_id_from(record)
        if not agent_session_id:
            return
        try:
            from claude_core_lib.claude_core_lib.session.history import (
                delete_session_file,
            )
            if delete_session_file(agent_session_id):
                self.logger.info(
                    'deleted Claude transcript %s for forgotten task %s',
                    agent_session_id,
                    task_id,
                )
        except Exception:
            self.logger.exception(
                'failed deleting Claude transcript %s for task %s',
                agent_session_id,
                task_id,
            )

    def _delete_persisted_record(self, task_id: str) -> None:
        # Delete EVERY state file that maps to this task's canonical
        # key, not just the canonical lowercased path. Records written
        # before ``_record_path`` started lowercasing live under the
        # original-case filename (e.g. ``UNA-1201.json``); the
        # canonical path is ``una-1201.json``. Unlinking only the
        # canonical path left the legacy-cased file on disk, and
        # ``_load_persisted_records`` (a blanket ``glob('*.json')``)
        # then resurrected the task's tab on every restart — the
        # "task is back after restart" bug. Case-insensitive filename
        # match cleans both the canonical and any legacy variant.
        key = self._lookup_key(task_id).replace('/', '_').replace(os.sep, '_')
        targets = {self._record_path(task_id)}
        try:
            for candidate in self._state_dir.glob('*.json'):
                if candidate.stem.lower() == key:
                    targets.add(candidate)
        except OSError:
            # Directory listing failed — fall back to just the
            # canonical path below.
            pass
        for path in targets:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                self.logger.warning(
                    'failed to remove planning session record %s for task %s: %s',
                    path,
                    task_id,
                    exc,
                )

    def _load_persisted_records(self) -> None:
        if not self._state_dir.exists():
            return
        for path in sorted(self._state_dir.glob('*.json')):
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as exc:
                self.logger.warning(
                    'skipping unreadable planning session record %s: %s',
                    path,
                    exc,
                )
                continue
            if not isinstance(payload, dict):
                continue
            record = PlanningSessionRecord.from_dict(payload)
            if not record.task_id:
                continue
            # On startup the live subprocess is gone; reflect that so the
            # UI doesn't claim a tab is "active" when there's no subprocess
            # behind it. The agent_service cleanup loop will sweep these
            # records on the next scan for tasks that no longer need them.
            if record.status == SESSION_STATUS_ACTIVE:
                record.status = SESSION_STATUS_TERMINATED
                record.updated_at_epoch = time.time()
            # Key by lowercased task_id so case-mismatched lookups
            # find the same record. ``record.task_id`` itself keeps
            # its original case from disk for display purposes.
            self._records[self._lookup_key(record.task_id)] = record

    def _with_refreshed_session_id(
        self,
        record: PlanningSessionRecord | None,
    ) -> PlanningSessionRecord | None:
        if record is None:
            return None
        lookup_key = self._lookup_key(record.task_id)
        session = self._sessions.get(lookup_key)
        if session is None:
            return record
        if getattr(session, 'is_alive', False):
            drifted = self._discard_if_session_id_drifted_locked(
                lookup_key, record.task_id, session,
            )
            if drifted:
                return record
        live_id = read_session_id_from(session)
        record_id = read_session_id_from(record)
        if has_session_id(live_id) and not same_session_id(live_id, record_id):
            if record_id:
                self.logger.warning(
                    'task %s: live Claude reports session id %s, but '
                    'record is pinned to %s; keeping the persisted id',
                    record.task_id, live_id, record_id,
                )
                return record
            record.agent_session_id = live_id
            record.updated_at_epoch = time.time()
            self._persist_record(record)
        return record

    def _discard_if_session_id_drifted_locked(
        self,
        lookup_key: str,
        normalized_task_id: str,
        session: StreamingClaudeSession,
    ) -> bool:
        record = self._records.get(lookup_key)
        pinned_id = read_session_id_from(record)
        if not pinned_id:
            return False
        live_id = read_session_id_from(session)
        if same_session_id(live_id, pinned_id):
            return False
        self._sessions.pop(lookup_key, None)
        self.logger.warning(
            'task %s: live Claude session id %s disagrees with pinned id %s; '
            'terminating live process so the next spawn resumes the pinned id',
            normalized_task_id, live_id or '(blank)', pinned_id,
        )
        try:
            session.terminate()
        except Exception:
            self.logger.exception(
                'failed to terminate mismatched live session for task %s',
                normalized_task_id,
            )
        return True
