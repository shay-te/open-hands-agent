// Tests for useCommentStatusMap — polls a task's diff comments into a
// Map(commentStatusKey -> kato_status) so the chat's comment-run sticky
// prompt can tint its jump icon by the live status. Contract:
//   - Empty map until a fetch lands.
//   - Disabled (no comment-run prompt on screen) → never fetches.
//   - Builds the location-keyed map from the fetched comments.
//   - A task switch drops the previous task's statuses immediately.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchTaskComments: vi.fn(),
}));

import { fetchTaskComments } from '../api.js';
import { commentStatusKey } from '../utils/commentStatus.js';
import { useCommentStatusMap } from './useCommentStatusMap.js';


beforeEach(() => {
  fetchTaskComments.mockReset();
});


describe('useCommentStatusMap', () => {

  test('starts empty', () => {
    fetchTaskComments.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useCommentStatusMap('T1', true));
    expect(result.current.size).toBe(0);
  });

  test('does not fetch while disabled', () => {
    const { result } = renderHook(() => useCommentStatusMap('T1', false));
    expect(fetchTaskComments).not.toHaveBeenCalled();
    expect(result.current.size).toBe(0);
  });

  test('does not fetch without a task id', () => {
    renderHook(() => useCommentStatusMap('', true));
    expect(fetchTaskComments).not.toHaveBeenCalled();
  });

  test('builds the location-keyed status map from fetched comments', async () => {
    fetchTaskComments.mockResolvedValue({
      ok: true,
      body: { comments: [
        { file_path: 'src/a.js', line: 5, kato_status: 'in_progress' },
        { file_path: 'src/b.js', line: 9, kato_status: 'addressed' },
      ] },
    });
    const { result } = renderHook(() => useCommentStatusMap('T1', true));

    await waitFor(() => expect(result.current.size).toBe(2));
    expect(result.current.get(commentStatusKey('src/a.js', 5))).toBe('in_progress');
    expect(result.current.get(commentStatusKey('src/b.js', 9))).toBe('addressed');
    expect(fetchTaskComments).toHaveBeenCalledWith('T1');
  });

  test('ignores a failed envelope (keeps the last good map empty)', async () => {
    fetchTaskComments.mockResolvedValue({ ok: false, error: 'boom' });
    const { result } = renderHook(() => useCommentStatusMap('T1', true));
    await waitFor(() => expect(fetchTaskComments).toHaveBeenCalled());
    expect(result.current.size).toBe(0);
  });

  test('drops the previous task statuses immediately on a task switch', async () => {
    fetchTaskComments.mockResolvedValue({
      ok: true,
      body: { comments: [{ file_path: 'src/a.js', line: 5, kato_status: 'queued' }] },
    });
    const { result, rerender } = renderHook(
      ({ taskId }) => useCommentStatusMap(taskId, true),
      { initialProps: { taskId: 'T1' } },
    );
    await waitFor(() => expect(result.current.size).toBe(1));

    // Switch tasks before the new fetch resolves: the stale T1 map must
    // not bleed into T2 — the hook returns empty until T2's own fetch lands.
    fetchTaskComments.mockReturnValue(new Promise(() => {}));
    rerender({ taskId: 'T2' });
    expect(result.current.size).toBe(0);
  });
});
