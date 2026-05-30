// Tests for ``useAutoSizeTextarea`` — the shared measure-and-grow
// hook behind the chat composer (MessageForm) and the diff/editor
// comment form (CommentWidgets).
//
// Proves the three load-bearing behaviors:
//   - Non-empty value → height resets to 'auto' then to scrollHeight.
//   - emptyHeight collapse: trimmed-empty DOM value snaps to the
//     fixed single-line height (chat composer), reading el.value (the
//     live DOM) rather than the React value arg.
//   - No emptyHeight → plain auto→scrollHeight even when empty
//     (comment form has no single-line collapse target).
//   - The returned resize fn is callable imperatively (MessageForm's
//     caret-restoration second call site).

import { describe, test, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { useAutoSizeTextarea } from './useAutoSizeTextarea.js';


function _textarea(value, scrollHeight = 84) {
  const el = document.createElement('textarea');
  el.value = value;
  Object.defineProperty(el, 'scrollHeight', {
    value: scrollHeight,
    configurable: true,
  });
  return el;
}


describe('useAutoSizeTextarea', () => {

  test('non-empty value grows the textarea to scrollHeight', () => {
    const el = _textarea('some typed text', 120);
    const ref = { current: el };
    renderHook(() => useAutoSizeTextarea(ref, 'some typed text'));
    expect(el.style.height).toBe('120px');
  });

  test('emptyHeight: trimmed-empty value snaps to the fixed height', () => {
    const el = _textarea('   ', 200);
    const ref = { current: el };
    renderHook(() =>
      useAutoSizeTextarea(ref, '   ', { emptyHeight: 'calc(1.4em + 16px)' }),
    );
    // Collapsed to the single-line height, NOT grown to scrollHeight.
    expect(el.style.height).toBe('calc(1.4em + 16px)');
  });

  test('emptyHeight reads the LIVE DOM value, not the React value arg', () => {
    // DOM has real content but the React arg says empty — the hook
    // must measure the DOM (the fragment-paste caret-restore case).
    const el = _textarea('client:src/auth.py', 1234);
    const ref = { current: el };
    renderHook(() =>
      useAutoSizeTextarea(ref, '', { emptyHeight: 'calc(1.4em + 16px)' }),
    );
    expect(el.style.height).toBe('1234px');
  });

  test('no emptyHeight: empty value still does plain auto→scrollHeight', () => {
    const el = _textarea('', 30);
    const ref = { current: el };
    renderHook(() => useAutoSizeTextarea(ref, ''));
    expect(el.style.height).toBe('30px');
  });

  test('returns a callable resize fn for imperative re-measure', () => {
    const el = _textarea('one line', 40);
    const ref = { current: el };
    const { result } = renderHook(() => useAutoSizeTextarea(ref, 'one line'));
    expect(typeof result.current).toBe('function');
    // Grow the content out-of-band, then re-measure imperatively.
    Object.defineProperty(el, 'scrollHeight', { value: 300, configurable: true });
    act(() => { result.current(); });
    expect(el.style.height).toBe('300px');
  });

  test('null ref is a no-op (does not throw)', () => {
    const ref = { current: null };
    expect(() =>
      renderHook(() => useAutoSizeTextarea(ref, 'x')),
    ).not.toThrow();
  });
});
