// Component-level tests for MessageForm. The helpers it calls
// (composerDraft.js) already have their own test suite; this file
// proves the React wiring is correct end-to-end:
//
//   - Mount with taskId → reads existing draft into the textarea.
//   - Typing → mirrors to localStorage on every keystroke.
//   - Unmount + remount with the same taskId → draft is back.
//   - Tab switch (different taskId) → tabs don't see each other's drafts.
//   - Submit clears both the visible textarea AND the persisted draft.
//
// These were previously covered ONLY at the helper level. The
// operator-reported bug ("I type then switch tabs then come back
// and my input is gone") is wiring, not helpers, so it lives here.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { createRef } from 'react';

import MessageForm from './MessageForm.jsx';
import { DRAFT_STORAGE_PREFIX } from '../utils/composerDraft.js';


function renderForm({ taskId = 'T1', onSubmit = vi.fn(), ...rest } = {}) {
  return {
    onSubmit,
    ...render(
      <MessageForm
        taskId={taskId}
        turnInFlight={false}
        onSubmit={onSubmit}
        {...rest}
      />,
    ),
  };
}


describe('MessageForm — draft persistence (operator scenario)', () => {

  test('hydrates from localStorage on mount when a draft exists for taskId', () => {
    window.localStorage.setItem(`${DRAFT_STORAGE_PREFIX}T1`, 'preserved draft');

    renderForm({ taskId: 'T1' });

    const textarea = screen.getByRole('textbox');
    expect(textarea).toHaveValue('preserved draft');
  });

  test('mirrors every keystroke into localStorage keyed by taskId', () => {
    renderForm({ taskId: 'T1' });

    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'in progress' } });

    expect(window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}T1`))
      .toBe('in progress');
  });

  test('full A → B → A scenario: switching tabs preserves both drafts', () => {
    // Mount tab A and type.
    const { unmount } = renderForm({ taskId: 'A' });
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'message-for-A' },
    });
    unmount();  // SessionDetail unmount on tab switch

    // Mount tab B and type.
    const { unmount: unmountB } = renderForm({ taskId: 'B' });
    expect(screen.getByRole('textbox')).toHaveValue('');  // B starts empty
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'message-for-B' },
    });
    unmountB();

    // Back to A — its draft must be intact.
    renderForm({ taskId: 'A' });
    expect(screen.getByRole('textbox')).toHaveValue('message-for-A');
  });

  test('submit clears both the textarea AND the persisted draft on success', async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    const { container } = renderForm({ taskId: 'T1', onSubmit });

    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'send this' } });
    expect(window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}T1`))
      .toBe('send this');

    // Form submit (Enter key) — Shift not held.
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });

    expect(onSubmit).toHaveBeenCalledWith('send this', []);
    // Submit is now async; wait for the post-await state clear.
    await new Promise((r) => setTimeout(r, 0));
    expect(textarea).toHaveValue('');
    expect(window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}T1`)).toBeNull();
  });

  test('Bug: draft + textarea survive when onSubmit returns false (send failed)', async () => {
    // Operator clicks Send → backend returns an error envelope →
    // SessionDetail's onSendMessage returns false. The draft must
    // stay intact so the operator can retry without retyping.
    const onSubmit = vi.fn().mockResolvedValue(false);
    renderForm({ taskId: 'T1', onSubmit });

    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'might fail' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });

    await new Promise((r) => setTimeout(r, 0));
    expect(textarea).toHaveValue('might fail');
    expect(window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}T1`))
      .toBe('might fail');
  });

  test('Bug: draft + textarea survive when onSubmit throws (network error)', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('network down'));
    renderForm({ taskId: 'T1', onSubmit });

    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'mid-flight' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });

    await new Promise((r) => setTimeout(r, 0));
    expect(textarea).toHaveValue('mid-flight');
    expect(window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}T1`))
      .toBe('mid-flight');
  });

  test('Shift+Enter inserts a newline and does NOT submit', () => {
    const onSubmit = vi.fn();
    renderForm({ taskId: 'T1', onSubmit });

    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'line 1' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });

    expect(onSubmit).not.toHaveBeenCalled();
  });

  test('imperative clear() wipes textarea AND localStorage', () => {
    const ref = createRef();
    render(
      <MessageForm
        ref={ref}
        taskId="T1"
        turnInFlight={false}
        onSubmit={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'about to clear' },
    });

    act(() => { ref.current.clear(); });

    expect(screen.getByRole('textbox')).toHaveValue('');
    expect(window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}T1`)).toBeNull();
  });

  test('imperative appendFragment merges into the existing draft', () => {
    const ref = createRef();
    render(
      <MessageForm
        ref={ref}
        taskId="T1"
        turnInFlight={false}
        onSubmit={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'please review' },
    });
    act(() => { ref.current.appendFragment('src/auth.py'); });

    expect(screen.getByRole('textbox')).toHaveValue('please review src/auth.py');
    // And the merged value is persisted too.
    expect(window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}T1`))
      .toBe('please review src/auth.py');
  });

  test('imperative appendFragment keeps the appended caret visible', () => {
    const ref = createRef();
    render(
      <MessageForm
        ref={ref}
        taskId="T1"
        turnInFlight={false}
        onSubmit={vi.fn()}
      />,
    );
    const textarea = screen.getByRole('textbox');
    Object.defineProperty(textarea, 'scrollHeight', {
      value: 1234,
      configurable: true,
    });
    textarea.focus = vi.fn();
    textarea.setSelectionRange = vi.fn();

    act(() => { ref.current.appendFragment('client:src/auth.py'); });

    const caret = 'client:src/auth.py'.length;
    expect(textarea.focus).toHaveBeenCalled();
    expect(textarea.setSelectionRange).toHaveBeenCalledWith(caret, caret);
    expect(textarea.scrollTop).toBe(1234);
  });

  test('empty composer starts as a single-line field', () => {
    renderForm({ taskId: 'T1' });

    const textarea = screen.getByRole('textbox');
    expect(textarea).toHaveAttribute('rows', '1');
    expect(textarea).toHaveAttribute('placeholder', 'Reply to Claude');
  });
});


describe('MessageForm — disabled + working states', () => {

  test('disabled prop blocks submission even on Enter', () => {
    const onSubmit = vi.fn();
    renderForm({
      taskId: 'T1', onSubmit,
      disabled: true,
      disabledReason: 'No record for this task on the server.',
    });

    const textarea = screen.getByRole('textbox');
    expect(textarea).toBeDisabled();
    // Even if somehow Enter fires, submit must be a no-op.
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  test('disabled placeholder shows the disabledReason', () => {
    renderForm({
      taskId: 'T1',
      disabled: true,
      disabledReason: 'No record for this task on the server.',
    });

    const textarea = screen.getByRole('textbox');
    expect(textarea).toHaveAttribute(
      'placeholder',
      expect.stringContaining('No record for this task'),
    );
  });

  test('Submit button label flips to "Queue" while turnInFlight is true', () => {
    renderForm({ taskId: 'T1', turnInFlight: true });
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'follow-up' },
    });
    // Mid-turn the composer queues instead of steering — the button
    // says "Queue" and carries the is-queued accent.
    const submitButton = screen.getByRole('button', { name: /queue/i });
    expect(submitButton).toBeInTheDocument();
    expect(submitButton).toHaveClass('is-queued');
  });

  test('Submit button is "Send" when not in flight', () => {
    renderForm({ taskId: 'T1', turnInFlight: false });
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'something' },
    });
    expect(screen.getByRole('button', { name: /^send$/i })).toBeInTheDocument();
  });

  test('Send button is disabled when textarea is empty and no attachments', () => {
    renderForm({ taskId: 'T1' });
    // No text typed, nothing attached. Submit must be disabled to
    // prevent accidental empty-message submission.
    const submitButton = screen.getByRole('button', { name: /^send$/i });
    expect(submitButton).toBeDisabled();
  });

  test('Send button becomes enabled once text is typed', () => {
    renderForm({ taskId: 'T1' });
    const submit = screen.getByRole('button', { name: /^send$/i });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'hi' } });
    expect(submit).not.toBeDisabled();
  });
});


describe('MessageForm — model selector', () => {

  test('renders a model selector when availableModels is non-empty', () => {
    renderForm({
      taskId: 'T1',
      availableModels: [
        { id: 'opus', label: 'Opus' },
        { id: 'sonnet', label: 'Sonnet' },
      ],
      selectedModel: 'opus',
    });

    const select = screen.getByRole('combobox', { name: /select model/i });
    expect(select).toBeInTheDocument();
    expect(select).toHaveValue('opus');
  });

  test('does NOT render a model selector when availableModels is empty', () => {
    renderForm({ taskId: 'T1', availableModels: [] });
    expect(screen.queryByRole('combobox', { name: /select model/i }))
      .not.toBeInTheDocument();
  });

  test('changing the selected model fires onModelChange', () => {
    const onModelChange = vi.fn();
    renderForm({
      taskId: 'T1',
      availableModels: [
        { id: 'opus', label: 'Opus' },
        { id: 'sonnet', label: 'Sonnet' },
      ],
      selectedModel: 'opus',
      onModelChange,
    });

    fireEvent.change(screen.getByRole('combobox', { name: /select model/i }), {
      target: { value: 'sonnet' },
    });
    expect(onModelChange).toHaveBeenCalledWith('sonnet');
  });
});
