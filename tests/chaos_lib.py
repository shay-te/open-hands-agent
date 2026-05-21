"""Shared fixtures for tests that exercise REAL services, not mocks.

Most of the previous coverage tests mocked the thing-under-test (a
LocalCommentStore stub instead of a real one, a SimpleNamespace task
runner instead of a real ThreadPoolExecutor). When the real impl
drifts those tests keep passing because the stub doesn't know it
diverged. The helpers in this module exist so a test can stand up a
real comment store on a real tempdir, drive a real ParallelTaskRunner,
and shove genuinely nasty input strings at it.

This file deliberately has no ``test_`` prefix so unittest discover
ignores it. Import from it; don't run it.
"""

from __future__ import annotations

import random
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable
from unittest.mock import MagicMock

from kato_core_lib.comment_core_lib import (
    CommentRecord,
    CommentSource,
    CommentStatus,
    KatoCommentStatus,
    LocalCommentStore,
)
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_core_lib.data_layers.service.parallel_task_runner import (
    ParallelTaskRunner,
)
from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_PROVISIONING,
    WORKSPACE_STATUS_REVIEW,
    WorkspaceRecord,
)
from workspace_core_lib.workspace_core_lib.data_layers.data_access.workspace_data_access import (
    WorkspaceDataAccess,
)
from workspace_core_lib.workspace_core_lib.data_layers.service.workspace_service import (
    WorkspaceService,
)


# ---------- human / impatient task strings ----------
#
# Replaces "Update client and backend APIs" / "Fix bug" boilerplate.
# These read like a tired engineer dumping their actual tickets at
# 4pm on a Friday.

IMPATIENT_TITLES: tuple[str, ...] = (
    'fix it',
    'whats wrong with you please fix it',
    'do it',
    'this is broken AGAIN',
    'why is the login broken',
    'help me!!!',
    'just make it work',
    'ugh another null pointer',
    'PLEASE deploy already',
    'why does this keep crashing',
    'fix the thing that was broken yesterday',
    'come on this should be simple',
    'no time. fix.',
    "i don't understand any of this just fix it",
    'kato do your job',
)

IMPATIENT_BODIES: tuple[str, ...] = (
    'it crashes when i click the button. fix it.',
    'this used to work last week i swear',
    'tested locally, broken on prod. usual.',
    'no idea why, look at the logs, do something',
    'whats wrong with you???',
    'the error is in the screenshot i sent on slack',
    'do whatever, just make the tests green',
    'see the linked ticket. or dont. fix it anyway.',
    'rolling back if not fixed by EOD',
    '',                                                # empty body — a real ticket sometimes has none
    '  ',                                              # whitespace-only
    'urgent. impacting customers. drop everything.',
)

IMPATIENT_COMMENTS: tuple[str, ...] = (
    'why is this still here?',
    'fix this please',
    'come on',
    'this is the third review',
    'you keep doing this',
    'just delete this whole function',
    'no.',
    'why',
    'do better',
    'pls',
)


def impatient_title(seed: int | None = None) -> str:
    rng = random.Random(seed)
    return rng.choice(IMPATIENT_TITLES)


def impatient_body(seed: int | None = None) -> str:
    rng = random.Random(seed)
    return rng.choice(IMPATIENT_BODIES)


def impatient_comment(seed: int | None = None) -> str:
    rng = random.Random(seed)
    return rng.choice(IMPATIENT_COMMENTS)


# ---------- chaos inputs ----------
#
# Real users paste 200KB of stack trace. Real ticket ids end up with
# slashes. Real comment bodies have RTL marks and zero-width joiners.
# Stress all of it.

EMOJI_HEAVY = '🔥💀💩🤡 fix THIS 🚒🚒🚒 🙏🙏🙏'
RTL_INJECTION = 'normal ‮ then reversed ‬ done'
ZERO_WIDTH_SOUP = 'hi​dden‌text‍﻿'
CONTROL_CHARS = 'tabbed\there\nand\rcr\x00null'
WEIRD_QUOTES = '"smart" “left” ‘right’ « guillemet »'
SQL_INJECTION = "'; DROP TABLE comments; --"
PATH_TRAVERSAL = '../../../etc/passwd'
HUGE_BODY = 'fix it. ' * 12_000          # ~100KB
NESTED_EMOJI = '\U0001f9d1‍\U0001f4bb' * 50  # ZWJ technologist sequence
MIXED_LANGUAGE = 'fix bug 修复错误 إصلاح الخطأ исправить ошибку'

CHAOS_BODIES: tuple[str, ...] = (
    EMOJI_HEAVY,
    RTL_INJECTION,
    ZERO_WIDTH_SOUP,
    CONTROL_CHARS,
    WEIRD_QUOTES,
    SQL_INJECTION,
    HUGE_BODY,
    NESTED_EMOJI,
    MIXED_LANGUAGE,
)

# Task ids that a sloppy human + a sloppy CSV importer can produce.
# Note: anything with a path separator is filtered by ``_safe_segment``
# inside WorkspaceDataAccess — use only safe-looking ones for any test
# that actually creates a workspace.
CHAOS_TASK_IDS_SAFE: tuple[str, ...] = (
    'PROJ-1',
    'proj-with-hyphens',
    'lower_snake_case',
    'MixedCase123',
    'task_with_emoji_in_summary_only',
    'TICKET-9999',
)


def chaos_body(seed: int | None = None) -> str:
    rng = random.Random(seed)
    return rng.choice(CHAOS_BODIES)


# ---------- real workspace / comment-store builders ----------


def build_real_workspace_service(root: Path) -> WorkspaceService:
    """Real WorkspaceService backed by a real on-disk tempdir.

    Use this instead of MagicMock for any test that exercises
    AgentService methods that hit ``_safe_list_workspaces`` or
    ``_comment_store_for`` — the real path now runs.
    """
    data_access = WorkspaceDataAccess(root=root)
    return WorkspaceService(data_access=data_access, max_parallel_tasks=4)


def materialize_workspace(
    workspace_service: WorkspaceService,
    task_id: str,
    *,
    status: str = WORKSPACE_STATUS_ACTIVE,
    summary: str = '',
    repository_ids: Iterable[str] = (),
) -> WorkspaceRecord:
    """Create a real workspace folder on disk and return its record."""
    record = workspace_service.create(
        task_id=task_id,
        task_summary=summary or impatient_title(seed=hash(task_id)),
        repository_ids=list(repository_ids) or ['repo-a'],
    )
    if status != WORKSPACE_STATUS_PROVISIONING:
        workspace_service.update_status(task_id, status)
        record = workspace_service.get(task_id)
    return record


def real_store_for(workspace_service: WorkspaceService, task_id: str) -> LocalCommentStore:
    """Real LocalCommentStore writing into the actual workspace folder."""
    return LocalCommentStore(workspace_service.workspace_path(task_id))


def queue_real_comment(
    workspace_service: WorkspaceService,
    task_id: str,
    *,
    body: str | None = None,
    kato_status: str = KatoCommentStatus.QUEUED.value,
    repo_id: str = 'repo-a',
    author: str = 'operator',
) -> CommentRecord:
    """Persist a real comment record on the real store on disk."""
    store = real_store_for(workspace_service, task_id)
    record = CommentRecord(
        repo_id=repo_id,
        body=body or impatient_comment(seed=hash(task_id)),
        author=author,
        source=CommentSource.LOCAL.value,
        status=CommentStatus.OPEN.value,
        kato_status=kato_status,
    )
    return store.add(record)


# ---------- real AgentService builder ----------


def _mock_required_services() -> dict:
    """The 6 required collaborators that test-targeted methods don't
    exercise. We mock these because building real TaskService /
    ImplementationService / TestingService / RepositoryService etc.
    requires HTTP creds, a git remote, and a model endpoint."""
    return dict(
        task_service=MagicMock(),
        task_state_service=MagicMock(),
        implementation_service=MagicMock(),
        testing_service=MagicMock(),
        repository_service=MagicMock(),
        notification_service=MagicMock(),
    )


def build_real_agent_service(
    workspace_root: Path,
    *,
    parallel_task_runner: ParallelTaskRunner | None = None,
    session_manager=None,
) -> tuple[AgentService, WorkspaceService]:
    """Construct an AgentService wired to a REAL workspace_manager.

    Returns ``(service, workspace_service)`` so callers can poke the
    workspace state directly. The service's ``_comment_store_for``
    and ``_safe_list_workspaces`` paths now run real code against
    real files on disk.

    Mocking is limited to:
      * The six required upstream services (task / impl / testing /
        repo / notify / task_state). These would need HTTP creds to
        construct; the methods we care about don't call into them.
      * Optional collaborators left at their defaults.
    """
    workspace_service = build_real_workspace_service(workspace_root)
    service = AgentService(
        workspace_manager=workspace_service,
        parallel_task_runner=parallel_task_runner,
        session_manager=session_manager,
        # ttl=0 disables the freshness window so a test that bumps
        # the clock doesn't accidentally evict its own workspace.
        review_workspace_ttl_seconds=0.0,
        **_mock_required_services(),
    )
    return service, workspace_service


# ---------- real parallel runner builder ----------


def build_real_runner(max_workers: int = 4) -> ParallelTaskRunner:
    """Real ParallelTaskRunner — not a SimpleNamespace stub."""
    return ParallelTaskRunner(max_workers=max_workers)


# ---------- helpers for the dispatch-path tests ----------


class _RealScanService(object):
    """Real-shaped service for ``process_assigned_tasks`` dispatch.

    The dispatch helpers only need 4 things on the service:
      * ``parallel_task_runner``
      * ``get_assigned_tasks()``
      * ``process_assigned_task(task)`` — the worker callable
      * ``get_new_pull_request_comments()``,
        ``task_id_for_review_comment(c)``,
        ``process_review_comment_batch(comments)``

    Implementing those directly (instead of MagicMock-ing them) makes
    the dispatch test exercise the actual ``submit → done_callback →
    drain`` cycle of the real runner.
    """

    def __init__(
        self,
        *,
        runner: ParallelTaskRunner | None = None,
        assigned_tasks: list | None = None,
        review_comments: list | None = None,
        process_result=None,
        review_batch_result: list | None = None,
        task_id_for_comment_fn=None,
    ) -> None:
        self.parallel_task_runner = runner
        self._assigned_tasks = list(assigned_tasks or [])
        self._review_comments = list(review_comments or [])
        self._process_result = process_result
        self._review_batch_result = list(review_batch_result or [])
        self._task_id_for_comment_fn = task_id_for_comment_fn
        # Counters so tests can assert real call counts without
        # having to wrap with Mock.
        self.process_calls: list = []
        self.batch_calls: list = []

    def get_assigned_tasks(self) -> list:
        return list(self._assigned_tasks)

    def process_assigned_task(self, task) -> object:
        self.process_calls.append(task)
        result = self._process_result
        if callable(result):
            return result(task)
        return result

    def get_new_pull_request_comments(self) -> list:
        return list(self._review_comments)

    def task_id_for_review_comment(self, comment) -> str:
        if callable(self._task_id_for_comment_fn):
            return self._task_id_for_comment_fn(comment)
        return str(getattr(comment, 'task_id', '') or '')

    def process_review_comment_batch(self, comments) -> list:
        self.batch_calls.append(list(comments))
        return list(self._review_batch_result)

    def process_review_comment(self, comment) -> dict:
        # Singular fallback path (legacy stubs).
        return {'comment_id': getattr(comment, 'comment_id', '')}


def make_task(task_id: str, *, summary: str | None = None) -> SimpleNamespace:
    """Minimal Task-shaped object with HUMAN inputs by default."""
    return SimpleNamespace(
        id=task_id,
        summary=summary or impatient_title(seed=hash(task_id)),
        description=impatient_body(seed=hash(task_id)),
        tags=[],
    )


def make_review_comment(
    *,
    comment_id: str,
    repository_id: str = 'r1',
    pull_request_id: str = 'pr-1',
    body: str | None = None,
) -> SimpleNamespace:
    obj = SimpleNamespace(
        comment_id=comment_id,
        body=body or impatient_comment(seed=hash(comment_id)),
    )
    setattr(obj, 'repository_id', repository_id)
    setattr(obj, 'pull_request_id', pull_request_id)
    return obj


# ---------- random-action driver (Python side, for stress tests) ----------


class ChaosActionDriver(object):
    """Run a set of named actions in a randomized order, deterministic by seed.

    Lets a stress test say "do these 5 things in some order I don't
    control, then assert the invariant". Catches order-dependency
    bugs that fixed-sequence tests miss.
    """

    def __init__(self, actions: dict, *, seed: int = 0) -> None:
        if not actions:
            raise ValueError('actions must be non-empty')
        self._actions = dict(actions)
        self._rng = random.Random(seed)

    def run_random(self, *, iterations: int = 50) -> list[str]:
        names = list(self._actions.keys())
        log: list[str] = []
        for _ in range(iterations):
            name = self._rng.choice(names)
            self._actions[name]()
            log.append(name)
        return log


def tempdir_path() -> Path:
    """Convenience for tests that just want a one-off tempdir."""
    return Path(tempfile.mkdtemp(prefix='kato-chaos-'))
