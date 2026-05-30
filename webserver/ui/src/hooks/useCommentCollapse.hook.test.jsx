import { describe, test, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useCommentCollapse } from './useCommentCollapse.js';

const KEY = 'kato.commentCollapse.v1';

beforeEach(() => { localStorage.clear(); });

describe('useCommentCollapse — collapse state that survives reload + kato restart', () => {
  test('with no stored choice, uses the status-derived default', () => {
    const { result } = renderHook(() => useCommentCollapse('c1', true));
    expect(result.current[0]).toBe(true);
  });

  test('toggle persists and is restored on a fresh mount (post-restart)', () => {
    const first = renderHook(() => useCommentCollapse('c1', false));
    expect(first.result.current[0]).toBe(false);
    act(() => { first.result.current[1](); });
    expect(first.result.current[0]).toBe(true);
    expect(JSON.parse(localStorage.getItem(KEY))).toEqual({ c1: true });
    first.unmount();

    // A fresh mount models the operator reopening the UI after a restart.
    const second = renderHook(() => useCommentCollapse('c1', false));
    expect(second.result.current[0]).toBe(true);
  });

  test('a stored choice wins over the default on mount', () => {
    localStorage.setItem(KEY, JSON.stringify({ c2: false }));
    const { result } = renderHook(() => useCommentCollapse('c2', true));
    expect(result.current[0]).toBe(false);
  });

  test('a status flip after mount re-syncs to the new default and persists', () => {
    const { result, rerender } = renderHook(
      ({ d }) => useCommentCollapse('c3', d),
      { initialProps: { d: false } },
    );
    expect(result.current[0]).toBe(false);
    rerender({ d: true }); // resolve -> default collapsed
    expect(result.current[0]).toBe(true);
    expect(JSON.parse(localStorage.getItem(KEY))).toEqual({ c3: true });
  });

  test('no comment id -> ephemeral, nothing persisted', () => {
    const { result } = renderHook(() => useCommentCollapse(null, false));
    act(() => { result.current[1](); });
    expect(result.current[0]).toBe(true);
    expect(localStorage.getItem(KEY)).toBeNull();
  });
});
