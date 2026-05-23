# Windows Lock-Semantics Verification

Proof that the `msvcrt`-based cross-process write locks in
`repository_approval_service.py` and `comment_core_lib/comment_store.py`
actually work on Windows. The branch was code-reviewed on macOS but had
never been executed on Windows before this run.

## Environment

| Field        | Value                                                          |
|--------------|----------------------------------------------------------------|
| Platform     | `Windows-10-10.0.26200-SP0` (Windows 11 24H2 build 26200)      |
| Python       | 3.11.9 (tags/v3.11.9:de54cf5, Apr 2 2024, MSC v.1938 64-bit)   |
| Architecture | AMD64                                                          |
| Repo state   | branch `bug_fixes`, working tree includes pending changes      |

## Locking primitives exercised

- `kato_core_lib/data_layers/service/repository_approval_service.py:_process_safe_write_lock` — calls `msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)` on the sidecar file, then `msvcrt.LK_UNLCK`.
- `kato_core_lib/comment_core_lib/comment_store.py:_process_safe_write_lock` — same shape (`LK_LOCK` → 1-byte at offset 0 → `LK_UNLCK`).

Both lock a single byte at offset 0. `LK_LOCK` blocks for ~10 s before
raising; the implementation retries forever so callers don't have to.

## Tests executed

```
.venv\Scripts\python.exe -m unittest \
    tests.test_repository_approval_real \
    tests.test_stress_concurrent.LocalCommentStoreCrossProcessTests -v
```

Result: **30 passed, 0 failed, 0 errors** in 4.773 s.

### `tests.test_repository_approval_real` — 28 tests

The ones that actually exercise the cross-process lock (rather than just
storage round-trips or input validation):

- `RepositoryApprovalConcurrentApprovalsTests.test_20_concurrent_approve_calls_all_persist` — 20 threads racing the same sidecar.
- `RepositoryApprovalConcurrentApprovalsTests.test_cross_instance_concurrent_approve_all_persist` — each thread builds its own service instance.
- `RepositoryApprovalConcurrentApprovalsTests.test_cross_process_concurrent_approve_all_persist` — separate OS processes racing the same sidecar.
- `RepositoryApprovalConcurrentApprovalsTests.test_cross_process_mixed_approve_revoke_converges` — approve and revoke racing on overlapping ids; final state is consistent.
- `RepositoryApprovalConcurrentApprovalsTests.test_interleaved_approve_and_revoke_converge_to_consistent_state`
- `RepositoryApprovalConcurrentApprovalsTests.test_long_lived_service_sees_revokes_from_another_process` — stale-cache detection on revoke.
- `RepositoryApprovalConcurrentApprovalsTests.test_long_lived_service_sees_writes_made_by_another_process` — stale-cache detection on approve.

Plus 21 chaos / corruption / lifecycle / module-helper / unapproved-filter
cases — all green.

### `tests.test_stress_concurrent.LocalCommentStoreCrossProcessTests` — 2 tests

- `test_cross_process_concurrent_add_all_persist` — twelve real Python processes adding to the same store; all 12 land.
- `test_long_lived_instance_sees_external_writes` — cached store drops its cache when another process writes.

## What this proves

1. `msvcrt.locking` with `LK_LOCK` and a 1-byte range at offset 0 **does**
   block writers from other processes on this Windows + NTFS combination.
2. Two writers do not both succeed — the second one waits.
3. Stale in-process caches are correctly invalidated after a cross-process
   write (mtime-based detection works on NTFS).
4. Approve/revoke convergence is correct under concurrent contention from
   separate Python interpreters, not just threads.

The macOS-only code review can be retired; the Windows branch is now
exercised end-to-end against the real filesystem and the real Win32
locking API.

## Notes for whoever re-runs this

- Tests use `tempfile.TemporaryDirectory()` for the sidecar/store path —
  no shared global state, safe to run in parallel with other suites.
- `LK_LOCK` waits ~10 s before raising on contention. Don't reduce the
  retry loop without re-checking these tests; that wait is the only
  reason concurrent runs survive when a process is briefly slow.
- Some tests emit a benign `approval sidecar at ... is unreadable or
  corrupt; treating as empty` log line — that's the corruption-tolerance
  path being exercised, not a failure.
