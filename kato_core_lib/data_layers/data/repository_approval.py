"""Approval records for the Restricted Execution Protocol (REP).

REP gates the *first* agent run against a repository: until an
operator has explicitly approved a repo id, kato refuses to run
agents against it. This module owns the data shape — storage and
runtime checks live in
``kato_core_lib.data_layers.service.repository_approval_service``.

Two approval modes:

- ``RESTRICTED``: default for newly-approved repos. Indicates the
  operator has approved the repo for use, but has not yet validated
  the agent's first run. The preflight gate refuses
  RESTRICTED-mode tasks if the global posture is weaker than
  required (docker-off / bypass-on / lenient scanner severity).
- ``TRUSTED``: operator explicitly elevated this repo after first-time
  review. Runs with the global config — same as any inventory repo.

Approval is opt-in only. There is **no** "auto-approve after N
successful tasks" — the operator approves explicitly, every time.
"""

from __future__ import annotations
from agent_core_lib.agent_core_lib.helpers.text_utils import text_from_mapping

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
class ApprovalMode(str, Enum):
    """Run-mode flag attached to an approval record.

    Inherits ``str`` so JSON serialisation hands back a plain string
    without a custom encoder.
    """

    RESTRICTED = 'restricted'
    TRUSTED = 'trusted'

    @classmethod
    def from_string(cls, value: str | None) -> ApprovalMode:
        normalised = (value or '').strip().lower()
        for mode in cls:
            if mode.value == normalised:
                return mode
        # Unknown / blank → safer default. Operator can elevate later
        # via the ``./kato approve-repo`` picker (answer "yes" to
        # the trusted-mode question on apply).
        return cls.RESTRICTED


@dataclass(frozen=True)
class RepositoryApproval:
    """One row in the approval sidecar.

    ``remote_url`` is captured at approval time so the service can
    detect "approved repo whose remote URL has changed" and force
    re-approval. ``approved_by`` is best-effort identity (operator
    email from ``KATO_OPERATOR_EMAIL`` env, falls back to ``$USER``)
    and is purely audit metadata — no auth decisions hang on it.
    """

    repository_id: str
    remote_url: str
    approved_at_epoch: float
    approved_by: str
    approval_mode: ApprovalMode = ApprovalMode.RESTRICTED

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload['approval_mode'] = self.approval_mode.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> RepositoryApproval:
        return cls(
            repository_id=text_from_mapping(payload, 'repository_id').lower(),
            remote_url=text_from_mapping(payload, 'remote_url'),
            approved_at_epoch=float(payload.get('approved_at_epoch', 0.0) or 0.0),
            approved_by=str(payload.get('approved_by', '') or ''),
            approval_mode=ApprovalMode.from_string(payload.get('approval_mode')),
        )


@dataclass(frozen=True)
class ApprovalSidecar:
    """In-memory image of the JSON sidecar file."""

    version: int = 1
    approved: tuple[RepositoryApproval, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            'version': self.version,
            'approved': [entry.to_dict() for entry in self.approved],
        }

    @classmethod
    def from_dict(cls, payload: dict | None) -> ApprovalSidecar:
        payload = payload or {}
        version = int(payload.get('version', 1) or 1)
        raw_entries = payload.get('approved', []) or []
        if not isinstance(raw_entries, list):
            raw_entries = []
        entries = tuple(
            RepositoryApproval.from_dict(entry)
            for entry in raw_entries
            if isinstance(entry, dict) and entry.get('repository_id')
        )
        return cls(version=version, approved=entries)


def now_epoch() -> float:
    return time.time()
