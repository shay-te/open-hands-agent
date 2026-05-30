// Tests for ``useSessionOption`` — the shared model/effort selector
// state block lifted out of SessionDetail. Proves the contract both
// pickers depend on:
//   - the option list is fetched ONCE (loadedRef guard), keyed by
//     ``optionsKey``,
//   - the current value is fetched per task and reset to '' with no
//     task, keyed by ``currentKey``,
//   - onChange optimistically sets local state THEN persists.

import { describe, test, expect, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

import { useSessionOption } from './useSessionOption.js';


function _config(overrides = {}) {
  return {
    fetchOptions: vi.fn().mockResolvedValue({ models: [{ id: 'm1', label: 'M1' }] }),
    optionsKey: 'models',
    fetchCurrent: vi.fn().mockResolvedValue({ model: 'm1' }),
    currentKey: 'model',
    setCurrent: vi.fn().mockResolvedValue({ ok: true }),
    ...overrides,
  };
}


describe('useSessionOption', () => {

  test('fetches the option list once and exposes it', async () => {
    const cfg = _config();
    const { result } = renderHook(() => useSessionOption('T1', cfg));
    await waitFor(() => expect(result.current[0]).toHaveLength(1));
    expect(result.current[0]).toEqual([{ id: 'm1', label: 'M1' }]);
    expect(cfg.fetchOptions).toHaveBeenCalledTimes(1);
  });

  test('does NOT re-fetch the list when the task changes', async () => {
    const cfg = _config();
    const { result, rerender } = renderHook(
      ({ id }) => useSessionOption(id, cfg),
      { initialProps: { id: 'T1' } },
    );
    await waitFor(() => expect(cfg.fetchOptions).toHaveBeenCalledTimes(1));
    rerender({ id: 'T2' });
    rerender({ id: 'T3' });
    // The catalogue is global — the loadedRef guard prevents re-fetch.
    expect(cfg.fetchOptions).toHaveBeenCalledTimes(1);
  });

  test('loads the current value for the bound task', async () => {
    const cfg = _config({ fetchCurrent: vi.fn().mockResolvedValue({ model: 'm9' }) });
    const { result } = renderHook(() => useSessionOption('T1', cfg));
    await waitFor(() => expect(result.current[1]).toBe('m9'));
    expect(cfg.fetchCurrent).toHaveBeenCalledWith('T1');
  });

  test('re-fetches the current value when the task changes', async () => {
    const fetchCurrent = vi.fn()
      .mockResolvedValueOnce({ model: 'a' })
      .mockResolvedValueOnce({ model: 'b' });
    const cfg = _config({ fetchCurrent });
    const { result, rerender } = renderHook(
      ({ id }) => useSessionOption(id, cfg),
      { initialProps: { id: 'T1' } },
    );
    await waitFor(() => expect(result.current[1]).toBe('a'));
    rerender({ id: 'T2' });
    await waitFor(() => expect(result.current[1]).toBe('b'));
  });

  test('resets the selection to empty when no task is bound', async () => {
    const cfg = _config();
    const { result, rerender } = renderHook(
      ({ id }) => useSessionOption(id, cfg),
      { initialProps: { id: 'T1' } },
    );
    await waitFor(() => expect(result.current[1]).toBe('m1'));
    rerender({ id: '' });
    await waitFor(() => expect(result.current[1]).toBe(''));
  });

  test('current value defaults to empty when the key is absent', async () => {
    const cfg = _config({ fetchCurrent: vi.fn().mockResolvedValue({}) });
    const { result } = renderHook(() => useSessionOption('T1', cfg));
    // No throw, stays ''.
    await waitFor(() => expect(cfg.fetchCurrent).toHaveBeenCalled());
    expect(result.current[1]).toBe('');
  });

  test('onChange sets local state optimistically, then persists', async () => {
    const cfg = _config();
    const { result } = renderHook(() => useSessionOption('T1', cfg));
    await waitFor(() => expect(result.current[1]).toBe('m1'));

    await act(async () => { await result.current[2]('m2'); });

    expect(result.current[1]).toBe('m2');
    expect(cfg.setCurrent).toHaveBeenCalledWith('T1', 'm2');
  });

  test('works for the effort shape (levels / effort keys)', async () => {
    const cfg = {
      fetchOptions: vi.fn().mockResolvedValue({ levels: ['low', 'high'] }),
      optionsKey: 'levels',
      fetchCurrent: vi.fn().mockResolvedValue({ effort: 'high' }),
      currentKey: 'effort',
      setCurrent: vi.fn().mockResolvedValue({ ok: true }),
    };
    const { result } = renderHook(() => useSessionOption('T1', cfg));
    await waitFor(() => expect(result.current[0]).toEqual(['low', 'high']));
    await waitFor(() => expect(result.current[1]).toBe('high'));
  });

  test('a non-array list result is ignored (stays empty)', async () => {
    const cfg = _config({ fetchOptions: vi.fn().mockResolvedValue({ models: null }) });
    const { result } = renderHook(() => useSessionOption('T1', cfg));
    await waitFor(() => expect(cfg.fetchOptions).toHaveBeenCalled());
    expect(result.current[0]).toEqual([]);
  });
});
