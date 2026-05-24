"""In-memory registry of active Kato planning sessions.

A session represents a Claude Code CLI conversation bound to a specific
Kato task id. The full streaming integration (subprocess management,
WebSocket I/O, permission-prompt handling) lands in a follow-up. For now
this module defines the shape and exposes a no-op registry that the
Flask app can render against.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from agent_core_lib.agent_core_lib.helpers.session_id_utils import (
    AGENT_SESSION_ID,
)


@dataclass
class PlanningSession(object):
    task_id: str
    task_summary: str
    status: str = "waiting"  # waiting | active | done
    created_at_epoch: float = field(default_factory=time.time)
    agent_session_id: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "task_summary": self.task_summary,
            "status": self.status,
            "created_at_epoch": self.created_at_epoch,
            AGENT_SESSION_ID: self.agent_session_id,
        }


class SessionRegistry(object):
    """Thread-safe in-memory store of planning sessions keyed by task id."""

    def __init__(self) -> None:
        self._sessions: dict[str, PlanningSession] = {}
        self._lock = threading.Lock()

    def upsert(self, session: PlanningSession) -> None:
        with self._lock:
            self._sessions[session.task_id] = session

    def get_session(self, task_id: str) -> dict[str, object] | None:
        with self._lock:
            session = self._sessions.get(task_id)
            return session.to_dict() if session else None

    def list_sessions(self) -> list[dict[str, object]]:
        with self._lock:
            return [session.to_dict() for session in self._sessions.values()]

    def remove(self, task_id: str) -> None:
        with self._lock:
            self._sessions.pop(task_id, None)
