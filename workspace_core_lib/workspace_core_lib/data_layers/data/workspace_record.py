"""On-disk metadata describing one task's workspace.

Pure data — no I/O, no business logic. Lives at the bottom of the
onion: data_access reads/writes it, service composes it into the
public API, and consumers see it through the service.

A *workspace* is a folder named after a task id (e.g. ``PROJ-123/``)
that contains a fresh clone of every repository the task touches.
The folder is the unit; this dataclass is the metadata that travels
inside it as ``<workspace>/<metadata-filename>``.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from agent_core_lib.agent_core_lib.helpers.session_id_utils import (
    read_session_id_from_mapping,
)
from agent_core_lib.agent_core_lib.helpers.text_utils import text_from_mapping


WORKSPACE_STATUS_PROVISIONING = 'provisioning'
WORKSPACE_STATUS_ACTIVE = 'active'
WORKSPACE_STATUS_REVIEW = 'review'
WORKSPACE_STATUS_DONE = 'done'
WORKSPACE_STATUS_ERRORED = 'errored'
WORKSPACE_STATUS_TERMINATED = 'terminated'

SUPPORTED_WORKSPACE_STATUSES = frozenset(
    {
        WORKSPACE_STATUS_PROVISIONING,
        WORKSPACE_STATUS_ACTIVE,
        WORKSPACE_STATUS_REVIEW,
        WORKSPACE_STATUS_DONE,
        WORKSPACE_STATUS_ERRORED,
        WORKSPACE_STATUS_TERMINATED,
    }
)


@dataclass
class WorkspaceRecord(object):
    """Metadata for one task workspace.

    Field meanings:

    * ``task_id`` — caller-supplied identifier, also the folder name
      under the workspace root.
    * ``task_summary`` — free-form human label, displayed by UIs.
    * ``status`` — lifecycle bucket; one of ``SUPPORTED_WORKSPACE_STATUSES``.
    * ``repository_ids`` — names of repos cloned into the workspace
      (each ends up at ``<workspace>/<repository_id>/``).
    * ``agent_session_id`` — opaque id of the agent conversation
      bound to this workspace (e.g. a Claude session uuid). Optional.
      Generic on purpose: this lib doesn't care which agent.
    * ``cwd`` — absolute path the agent was last spawned at. Used by
      consumers that need to resume an agent session keyed by cwd.
    * ``resume_on_startup`` — whether a host process should rehydrate
      this workspace on boot (true by default; UIs may toggle to
      false to "park" a workspace).
    * ``created_at_epoch`` / ``updated_at_epoch`` — wall-clock stamps.
    """

    task_id: str
    task_summary: str = ''
    status: str = WORKSPACE_STATUS_PROVISIONING
    repository_ids: list[str] = field(default_factory=list)
    agent_session_id: str = ''
    cwd: str = ''
    resume_on_startup: bool = True
    created_at_epoch: float = field(default_factory=time.time)
    updated_at_epoch: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> 'WorkspaceRecord':
        """Deserialize a record. Tolerant of partial payloads.

        Pass an empty / missing ``agent_session_id`` for graceful
        boot when an out-of-band tool wrote a partial JSON.
        """
        repository_ids_raw = payload.get('repository_ids') or []
        repository_ids = (
            [str(rid) for rid in repository_ids_raw if rid]
            if isinstance(repository_ids_raw, list)
            else []
        )
        agent_session_id = read_session_id_from_mapping(payload)
        return cls(
            task_id=str(payload.get('task_id', '') or ''),
            task_summary=str(payload.get('task_summary', '') or ''),
            status=str(
                payload.get('status', WORKSPACE_STATUS_PROVISIONING)
                or WORKSPACE_STATUS_PROVISIONING,
            ),
            repository_ids=repository_ids,
            agent_session_id=agent_session_id,
            cwd=text_from_mapping(payload, 'cwd'),
            resume_on_startup=bool(payload.get('resume_on_startup', True)),
            created_at_epoch=float(
                payload.get('created_at_epoch', time.time()) or time.time(),
            ),
            updated_at_epoch=float(
                payload.get('updated_at_epoch', time.time()) or time.time(),
            ),
        )
