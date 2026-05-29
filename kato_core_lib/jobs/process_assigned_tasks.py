from __future__ import annotations

from core_lib.jobs.job import Job

from kato_core_lib.data_layers.data.fields import (
    PullRequestFields,
    ReviewCommentFields,
    StatusFields,
)
from kato_core_lib.helpers.error_handling_utils import log_and_notify_failure
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.kato_core_lib import KatoCoreLib


def collect_processing_results(service) -> list[dict]:
    """Run a scan cycle.

    Tasks fan out across the parallel runner (when wired) so multiple
    tasks run concurrently up to ``KATO_MAX_PARALLEL_TASKS``. The scan
    loop itself stays single-threaded — it polls the ticket system,
    decides which tasks to start, and submits them. Review comments
    fan out the same way: each comment's review-fix is submitted under
    its task id, so cross-task fixes run concurrently while same-task
    fixes serialize via the runner's per-task dedup lock.
    """
    results = _dispatch_assigned_tasks(service)
    results.extend(_dispatch_review_comments(service))
    results.extend(_advance_finished_local_comment_runs(service))
    results.extend(_drain_queued_local_comments(service))
    return results


def _best_effort_drain(service, method_name: str, failure_log_message: str) -> list[dict]:
    """Best-effort call of ``service.<method_name>()`` returning a result list.

    Returns ``[]`` when the method is missing/non-callable or raises (logging
    ``failure_log_message``). Only a real list/tuple counts — a Mock service
    (tests) returns a Mock, so anything non-list is treated as "nothing
    drained" rather than blowing up the scan cycle on ``list(Mock())``.
    """
    operation = getattr(service, method_name, None)
    if not callable(operation):
        return []
    try:
        result = operation()
    except Exception:
        configure_logger(__name__).exception(failure_log_message)
        return []
    return list(result) if isinstance(result, (list, tuple)) else []


def _advance_finished_local_comment_runs(service) -> list[dict]:
    """Scan-loop fallback: complete/requeue IN_PROGRESS comments whose session ended.

    Without this, a comment stays "⟳ kato working" if no SSE subscriber
    was watching when the turn's RESULT event fired.
    """
    return _best_effort_drain(
        service,
        'advance_finished_comment_runs',
        'advance_finished_comment_runs failed; retrying next scan tick',
    )


def _drain_queued_local_comments(service) -> list[dict]:
    """Server-side drain of operator-queued local diff comments.

    Browser-independent: without this, a comment queued while Claude
    was busy only got dispatched if a browser SSE happened to be
    watching that task when the turn ended — otherwise it sat
    ``QUEUED`` indefinitely. Running it on every scan tick guarantees
    pickup on the next idle transition. Best-effort: a failure here
    must never abort the scan cycle.
    """
    return _best_effort_drain(
        service,
        'drain_all_queued_task_comments',
        'queued local-comment drain pass failed; retrying next scan tick',
    )


def _dispatch_assigned_tasks(service) -> list[dict]:
    """Submit each assigned task; collect results from already-finished workers."""
    runner = getattr(service, 'parallel_task_runner', None)
    assigned_tasks = service.get_assigned_tasks()
    if not _runner_has_real_concurrency(runner):
        # Legacy / single-worker path: run inline so the scan loop blocks
        # until each task is fully processed (preserves the original
        # behavior for setups with KATO_MAX_PARALLEL_TASKS=1, and keeps
        # mocked test setups using sync semantics).
        return _process_inline(service, assigned_tasks)
    # Submit-then-don't-block: a future-completed task's result lands in
    # ``results``; everything else continues running until the next scan.
    submitted_futures = []
    for task in assigned_tasks:
        if runner.is_in_flight(str(task.id)):
            continue
        future = runner.submit(
            str(task.id),
            (lambda t=task: service.process_assigned_task(t)),
        )
        if future is not None:
            submitted_futures.append(future)
    return _drain_finished_futures(submitted_futures)


def _runner_has_real_concurrency(runner) -> bool:
    """True only when ``runner`` is a real ParallelTaskRunner with > 1 worker.

    Guards against test mocks where ``runner.max_workers`` is a Mock
    (truthy, not int-comparable) and against single-worker production
    setups where the inline path is the same effective behavior with
    fewer moving parts.
    """
    if runner is None:
        return False
    max_workers = getattr(runner, 'max_workers', None)
    if not isinstance(max_workers, int):
        return False
    return max_workers > 1


def _process_inline(service, assigned_tasks) -> list[dict]:
    results: list[dict] = []
    for task in assigned_tasks:
        result = service.process_assigned_task(task)
        if result is not None:
            results.append(result)
    return results


def _drain_finished_futures(futures) -> list[dict]:
    """Return results for futures that already completed; let others keep running.

    The scan loop ticks every ~30s, so a long task that's still running
    just gets reported next cycle. Failures bubble out as exceptions
    here so the caller's existing error-handling can log + notify.
    """
    results: list[dict] = []
    for future in futures:
        if not future.done():
            continue
        try:
            result = future.result(timeout=0)
        except Exception:
            # Surface so log_and_notify_failure handles it consistently
            # with the legacy path.
            raise
        if result is not None:
            results.append(result)
    return results


def _process_review_comment_batch_best_effort(service, comments) -> list[dict]:
    """Run a batch through the service, fall back to singular per-comment.

    Production services expose ``process_review_comment_batch`` and
    return ``list[dict]``. Older test stubs (and any custom service
    that hasn't migrated) only expose ``process_review_comment``;
    fan out so behaviour stays correct even without the batched
    optimisation.
    """
    logger = configure_logger(__name__)
    batch_method = getattr(service, 'process_review_comment_batch', None)
    if callable(batch_method):
        try:
            result = batch_method(comments)
        except Exception:
            # Log so the operator triaging "review comments not being
            # processed" has a trail to follow. Returning ``[]`` keeps
            # the scan loop's best-effort contract (we'll retry next
            # tick) but the silent swallow hid every transient failure.
            logger.exception(
                'review-comment batch failed; retrying on the next scan tick',
            )
            return []
        # Real service returns list[dict]. A test Mock will auto-create
        # an attribute that returns another Mock — fall through to the
        # singular path so those tests keep working.
        if isinstance(result, list):
            return [entry for entry in result if entry is not None]
    results: list[dict] = []
    for comment in comments:
        try:
            single = service.process_review_comment(comment)
        except Exception:
            # Same diagnostic gap as the batch path: a silent skip
            # leaves the operator with no signal that a particular
            # comment is repeatedly failing. Log it.
            logger.exception(
                'review-comment singular processing failed for comment %s',
                getattr(comment, 'comment_id', '<unknown>'),
            )
            continue
        if single is not None:
            results.append(single)
    return results


def _group_review_comments_by_pull_request(comments) -> list[list]:
    """Bucket comments by ``(repository_id, pull_request_id)``.

    Comments on the same PR share the workspace clone, the branch,
    and (after batching) the agent spawn. Order within a bucket
    preserves the order ``get_new_pull_request_comments`` returned —
    which is roughly chronological — so the agent sees comments in
    the same order the reviewer wrote them. Bucket-list order
    follows first-occurrence so two PRs in the scan tick produce a
    stable processing order.
    """
    buckets: dict[tuple[str, str], list] = {}
    bucket_order: list[tuple[str, str]] = []
    for comment in comments:
        repository_id = str(
            getattr(comment, PullRequestFields.REPOSITORY_ID, '') or '',
        ).strip()
        pull_request_id = str(
            getattr(comment, ReviewCommentFields.PULL_REQUEST_ID, '') or '',
        ).strip()
        key = (repository_id, pull_request_id)
        if key not in buckets:
            buckets[key] = []
            bucket_order.append(key)
        buckets[key].append(comment)
    return [buckets[key] for key in bucket_order]


def _dispatch_review_comments(service) -> list[dict]:
    """Group review comments by PR, submit each group as a single batch.

    The runner's per-task dedup lock still serialises same-task work
    (two parallel batches on the same task can't fight for the
    workspace), and cross-task batches run concurrently. The batch
    itself collapses N agent spawns into 1 per PR — the cost saving
    that motivated this whole change.

    Without a real parallel runner we fall back to inline submission
    (preserves the single-worker / mocked-test path).
    """
    runner = getattr(service, 'parallel_task_runner', None)
    comments = service.get_new_pull_request_comments()
    grouped = _group_review_comments_by_pull_request(comments)
    if not _runner_has_real_concurrency(runner):
        results: list[dict] = []
        for batch in grouped:
            results.extend(
                _process_review_comment_batch_best_effort(service, batch),
            )
        return results
    submitted_futures = []
    for batch in grouped:
        task_id = service.task_id_for_review_comment(batch[0])
        if not task_id:
            results = _process_review_comment_batch_best_effort(service, batch)
            for result in results:
                submitted_futures.append(_completed_future(result))
            continue
        if runner.is_in_flight(task_id):
            continue
        future = runner.submit(
            task_id,
            (lambda b=batch: _process_review_comment_batch_best_effort(service, b)),
        )
        if future is not None:  # pragma: no branch - TOCTOU guard; is_in_flight check above already filters
            submitted_futures.append(future)
    return _drain_finished_review_batches(submitted_futures)


def _drain_finished_review_batches(futures) -> list[dict]:
    """Drain futures whose result is ``list[dict]`` (one per comment).

    The runner returns futures wrapping the batch result list; flatten
    so the caller's per-comment counting / logging stays unchanged.
    """
    drained = _drain_finished_futures(futures)
    flat: list[dict] = []
    for entry in drained:
        if isinstance(entry, list):
            flat.extend(entry)
        elif entry is not None:  # pragma: no branch - _drain_finished_futures already filters None
            flat.append(entry)
    return flat


def _completed_future(value):
    """Wrap an already-computed value in a Future so the drain code stays uniform."""
    from concurrent.futures import Future

    future: Future = Future()
    future.set_result(value)
    return future


class ProcessAssignedTasksJob(Job):
    def __init__(self) -> None:
        self.logger = configure_logger(self.__class__.__name__)

    def initialized(self, data_handler: KatoCoreLib) -> None:
        assert isinstance(data_handler, KatoCoreLib)
        self._data_handler = data_handler

    def run(self) -> None:
        try:
            results = collect_processing_results(self._data_handler.service)
            self._log_scan_results(results)
        except Exception as exc:
            log_and_notify_failure(
                logger=self.logger,
                notification_service=self._data_handler.service.notification_service,
                operation_name='process_assigned_task_job',
                error=exc,
                failure_log_message='process_assigned_tasks_job failed',
                notification_failure_log_message=(
                    'failed to send failure notification for process_assigned_task_job'
                ),
            )
            raise

    def _log_scan_results(self, results: list[dict]) -> None:
        results_to_log = [
            result
            for result in results
            if result.get(StatusFields.STATUS) != StatusFields.SKIPPED
        ]
        if results_to_log:
            self.logger.info(
                'completed processing results:\n%s',
                format_processing_results(results_to_log),
            )


def format_processing_results(results: list[dict]) -> str:
    return '\n'.join(
        f'- {_format_processing_result(result)}'
        for result in results
    )


def _format_processing_result(result: dict) -> str:
    status = str(result.get('status', 'unknown'))
    pull_request_id = result.get('pull_request_id')
    branch_name = result.get('branch_name')
    repository_id = result.get('repository_id')

    details: list[str] = [status]
    if pull_request_id:
        details.append(f'PR #{pull_request_id}')
    if branch_name:
        details.append(f'branch {branch_name}')
    if repository_id:
        details.append(f'repository {repository_id}')

    return ' | '.join(details)
