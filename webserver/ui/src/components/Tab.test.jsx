// Tests for Tab. Renders one task tab in the sidebar list: task id,
// summary, status dot, optional commit indicator, forget (X) button.
// onSelect fires on click; onForget fires on X click (after a
// window.confirm). The active prop drives styling.

import { describe, test, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

import Tab from './Tab.jsx';
import { TAB_STATUS } from '../constants/tabStatus.js';


function _session(overrides = {}) {
  return {
    task_id: 'KATO-123',
    task_summary: 'Fix the bug',
    status: TAB_STATUS.ACTIVE,
    working: true,
    has_changes_pending: false,
    live: true,
    agent_session_id: 'sess-1',
    ...overrides,
  };
}


describe('Tab', () => {

  test('pill shows the task id only (summary lives in the hover card)', () => {
    const { container } = render(
      <Tab session={_session()} onSelect={() => {}} />,
    );
    expect(screen.getByText('KATO-123')).toBeInTheDocument();
    // The summary is intentionally NOT in the pill — it moved to
    // the on-hover TabTooltip so wide titles don't shove neighbours
    // off the strip. No card is mounted until hover.
    expect(container.querySelector('li')).not.toHaveTextContent('Fix the bug');
    expect(document.querySelector('.tab-tooltip')).toBeNull();
  });

  test('clicking the tab fires onSelect with the task id', () => {
    const onSelect = vi.fn();
    render(<Tab session={_session()} onSelect={onSelect} />);
    fireEvent.click(screen.getByText('KATO-123'));
    expect(onSelect).toHaveBeenCalledWith('KATO-123');
  });

  test('active prop adds the active class', () => {
    const { container } = render(
      <Tab session={_session()} active={true} onSelect={() => {}} />,
    );
    expect(container.querySelector('li')).toHaveClass('active');
  });

  test('needsAttention prop adds the needs-attention class', () => {
    const { container } = render(
      <Tab session={_session()} needsAttention={true} onSelect={() => {}} />,
    );
    expect(container.querySelector('li')).toHaveClass('needs-attention');
  });

  test('status dot reflects the resolved status (attention overrides base)', () => {
    const { container } = render(
      <Tab session={_session({ status: TAB_STATUS.ACTIVE })} needsAttention={true} onSelect={() => {}} />,
    );
    // resolveTabStatus → ATTENTION when needsAttention is true.
    expect(container.querySelector('.status-dot')).toHaveClass(`status-${TAB_STATUS.ATTENTION}`);
  });

  test('working session keeps the clean resolved status dot', () => {
    const { container } = render(
      <Tab session={_session({ status: TAB_STATUS.REVIEW, working: true })} onSelect={() => {}} />,
    );
    const dot = container.querySelector('.status-dot');
    expect(dot).not.toHaveClass('is-working');
    expect(dot).toHaveClass(`status-${TAB_STATUS.WORKING}`);
    expect(dot).not.toHaveClass('is-idle-alive');
  });

  test('non-working session has no is-working class', () => {
    const { container } = render(
      <Tab session={_session({ working: false })} onSelect={() => {}} />,
    );
    const dot = container.querySelector('.status-dot');
    expect(dot).not.toHaveClass('is-working');
    // working:false on an ACTIVE tab is the "idle but alive" state.
    expect(dot).toHaveClass('is-idle-alive');
  });

  test('changes-pending indicator appears only when has_changes_pending is true', () => {
    const { container: c1 } = render(
      <Tab session={_session({ has_changes_pending: false })} onSelect={() => {}} />,
    );
    expect(c1.querySelector('.tab-changes-indicator')).toBeNull();

    const { container: c2 } = render(
      <Tab session={_session({ has_changes_pending: true })} onSelect={() => {}} />,
    );
    expect(c2.querySelector('.tab-changes-indicator')).toBeInTheDocument();
  });

  test('clicking forget button requests forget via onForget(task_id)', () => {
    // No native confirm anymore — the hard-confirm lives in
    // ForgetTaskModal at App level. Tab just hands off the id.
    const onSelect = vi.fn();
    const onForget = vi.fn();
    const confirmSpy = vi.spyOn(window, 'confirm');
    render(<Tab session={_session()} onSelect={onSelect} onForget={onForget} />);

    fireEvent.click(screen.getByLabelText('Forget this task'));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(onForget).toHaveBeenCalledWith('KATO-123');
    // event.stopPropagation in handleForget — onSelect must not fire.
    expect(onSelect).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  test('forget button is a no-op when onForget is not a function', () => {
    render(<Tab session={_session()} onSelect={() => {}} />);
    // Should not throw — handleForget bails when typeof
    // onForget !== 'function'.
    expect(() =>
      fireEvent.click(screen.getByLabelText('Forget this task')),
    ).not.toThrow();
  });

  test('missing task_summary renders without crashing', () => {
    const { container } = render(
      <Tab session={_session({ task_summary: null })} onSelect={() => {}} />,
    );
    // No summary <p> in the pill anymore; id still shows.
    expect(container.querySelector('li')).toBeInTheDocument();
    expect(screen.getByText('KATO-123')).toBeInTheDocument();
  });

  test('hover shows the designed tooltip card after the delay', () => {
    vi.useFakeTimers();
    try {
      const { container } = render(
        <Tab
          session={_session({ branch_name: 'feature/x' })}
          onSelect={() => {}}
        />,
      );
      const li = container.querySelector('li');
      fireEvent.mouseEnter(li);
      // Nothing before the hover delay elapses.
      expect(document.querySelector('.tab-tooltip')).toBeNull();
      act(() => { vi.advanceTimersByTime(400); });
      const card = document.querySelector('.tab-tooltip');
      expect(card).toBeInTheDocument();
      // Card carries the structured facts: id, summary, a Branch row.
      expect(card).toHaveTextContent('KATO-123');
      expect(card).toHaveTextContent('Fix the bug');
      expect(card).toHaveTextContent('Branch');
      expect(card).toHaveTextContent('feature/x');
      // Mouse leave tears the card down.
      fireEvent.mouseLeave(li);
      expect(document.querySelector('.tab-tooltip')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  test('leaving before the delay never opens the card', () => {
    vi.useFakeTimers();
    try {
      const { container } = render(
        <Tab session={_session()} onSelect={() => {}} />,
      );
      const li = container.querySelector('li');
      fireEvent.mouseEnter(li);
      fireEvent.mouseLeave(li);
      act(() => { vi.advanceTimersByTime(400); });
      expect(document.querySelector('.tab-tooltip')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});


describe('Tab — pin button', () => {

  test('renders the pin button on every tab', () => {
    render(<Tab session={_session()} onSelect={() => {}} />);
    expect(screen.getByRole('button', { name: /pin this task/i })).toBeInTheDocument();
  });

  test('unpinned state advertises "Pin" + aria-pressed=false', () => {
    render(<Tab session={_session()} onSelect={() => {}} />);
    const btn = screen.getByRole('button', { name: /pin this task/i });
    expect(btn).toHaveAttribute('aria-pressed', 'false');
    expect(btn).not.toHaveClass('is-pinned');
  });

  test('pinned state advertises "Unpin" + aria-pressed=true + is-pinned class', () => {
    render(<Tab session={_session()} pinned={true} onSelect={() => {}} />);
    const btn = screen.getByRole('button', { name: /unpin this task/i });
    expect(btn).toHaveAttribute('aria-pressed', 'true');
    expect(btn).toHaveClass('is-pinned');
  });

  test('pinned prop adds is-pinned class to the <li>', () => {
    const { container } = render(
      <Tab session={_session()} pinned={true} onSelect={() => {}} />,
    );
    expect(container.querySelector('li')).toHaveClass('is-pinned');
  });

  test('clicking the pin button fires onTogglePin with the task id', () => {
    const onTogglePin = vi.fn();
    const onSelect = vi.fn();
    render(
      <Tab
        session={_session()}
        onSelect={onSelect}
        onTogglePin={onTogglePin}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /pin this task/i }));
    expect(onTogglePin).toHaveBeenCalledWith('KATO-123');
    // Must NOT also fire onSelect — pin button is its own action,
    // not a tab-activation click.
    expect(onSelect).not.toHaveBeenCalled();
  });

  test('pin click without an onTogglePin handler is a safe no-op', () => {
    // Don't crash when the host forgets to wire the handler — just
    // swallow the click.
    render(<Tab session={_session()} onSelect={() => {}} />);
    expect(() => {
      fireEvent.click(screen.getByRole('button', { name: /pin this task/i }));
    }).not.toThrow();
  });
});
