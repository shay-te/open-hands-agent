// Tests for TabList. Maps sessions to Tabs in a <ul> and renders
// the header buttons (Add task, Scan now). Empty state shows when
// the sessions list is empty.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import TabList from './TabList.jsx';
import { TAB_STATUS } from '../constants/tabStatus.js';
import {
  PINNED_TABS_STORAGE_KEY,
  readPinnedIds,
} from '../utils/pinnedTabs.js';


function _session(taskId, overrides = {}) {
  return {
    task_id: taskId,
    task_summary: `Summary ${taskId}`,
    status: TAB_STATUS.ACTIVE,
    working: false,
    live: true,
    agent_session_id: 'sess',
    ...overrides,
  };
}


describe('TabList', () => {

  test('renders each session as a Tab', () => {
    render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        activeTaskId="A-2"
        onSelect={() => {}}
      />,
    );
    expect(screen.getByText('A-1')).toBeInTheDocument();
    expect(screen.getByText('A-2')).toBeInTheDocument();
    expect(screen.getByText('A-3')).toBeInTheDocument();
  });

  test('marks the activeTaskId tab as active', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2')]}
        activeTaskId="A-2"
        onSelect={() => {}}
      />,
    );
    const tabs = container.querySelectorAll('li.tab');
    expect(tabs[0]).not.toHaveClass('active');
    expect(tabs[1]).toHaveClass('active');
  });

  test('active-tab auto-scroll moves the STRIP, never scrollIntoView', () => {
    // Regression: ``active.scrollIntoView()`` walks + scrolls every
    // scrollable ancestor, so selecting the rightmost tab dragged
    // the whole page left and clipped the file pane. The effect must
    // scroll only its own container.
    const scrollToSpy = vi.fn();
    const scrollIntoViewSpy = vi.fn();
    const origScrollTo = window.HTMLElement.prototype.scrollTo;
    const origSIV = window.HTMLElement.prototype.scrollIntoView;
    window.HTMLElement.prototype.scrollTo = scrollToSpy;
    window.HTMLElement.prototype.scrollIntoView = scrollIntoViewSpy;
    try {
      render(
        <TabList
          sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
          activeTaskId="A-3"
          onSelect={() => {}}
        />,
      );
      expect(scrollToSpy).toHaveBeenCalled();
      expect(scrollIntoViewSpy).not.toHaveBeenCalled();
    } finally {
      window.HTMLElement.prototype.scrollTo = origScrollTo;
      window.HTMLElement.prototype.scrollIntoView = origSIV;
    }
  });

  test('marks tabs in attentionTaskIds as needs-attention', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2')]}
        attentionTaskIds={new Set(['A-1'])}
        onSelect={() => {}}
      />,
    );
    const tabs = container.querySelectorAll('li.tab');
    expect(tabs[0]).toHaveClass('needs-attention');
    expect(tabs[1]).not.toHaveClass('needs-attention');
  });

  test('renders the empty-state copy when sessions is empty', () => {
    render(<TabList sessions={[]} onSelect={() => {}} />);
    expect(screen.getByText(/No tabs yet/)).toBeInTheDocument();
    // "+ Add task" appears as a strong inside the empty-state copy
    // (separate from the header button). Use the empty-state id to
    // disambiguate from the button's aria-label.
    expect(screen.getByText('+ Add task')).toBeInTheDocument();
  });

  test('renders the empty-state when sessions is undefined (defensive)', () => {
    render(<TabList sessions={undefined} onSelect={() => {}} />);
    expect(screen.getByText(/No tabs yet/)).toBeInTheDocument();
  });

  test('Add task button fires onOpenAddTask', () => {
    const onOpenAddTask = vi.fn();
    render(<TabList sessions={[]} onSelect={() => {}} onOpenAddTask={onOpenAddTask} />);
    fireEvent.click(screen.getByLabelText('Add a task'));
    expect(onOpenAddTask).toHaveBeenCalledTimes(1);
  });

  test('Scan now button fires onScanNow when enabled', () => {
    const onScanNow = vi.fn();
    render(
      <TabList sessions={[]} onSelect={() => {}} onScanNow={onScanNow} scanPending={false} />,
    );
    fireEvent.click(screen.getByLabelText('Scan now'));
    expect(onScanNow).toHaveBeenCalledTimes(1);
  });

  test('Scan now button is disabled while scanPending is true', () => {
    render(
      <TabList sessions={[]} onSelect={() => {}} onScanNow={() => {}} scanPending={true} />,
    );
    expect(screen.getByLabelText('Scan now')).toBeDisabled();
  });

  test('Scan now button is disabled when onScanNow is not provided', () => {
    render(<TabList sessions={[]} onSelect={() => {}} />);
    expect(screen.getByLabelText('Scan now')).toBeDisabled();
  });
});


describe('TabList — pinned tab ordering', () => {

  beforeEach(() => {
    // Each test owns its own pinned set; don't leak across cases.
    window.localStorage.removeItem(PINNED_TABS_STORAGE_KEY);
  });

  function tabOrder(container) {
    return Array.from(container.querySelectorAll('li.tab strong'))
      .map((el) => el.textContent);
  }

  test('with no pinned tabs the order matches the session input', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        onSelect={() => {}}
      />,
    );
    expect(tabOrder(container)).toEqual(['A-1', 'A-2', 'A-3']);
  });

  test('pinned tasks render at the left in pin order', () => {
    // Seed localStorage so the initial render already has the pins.
    window.localStorage.setItem(
      PINNED_TABS_STORAGE_KEY, JSON.stringify(['A-3', 'A-1']),
    );
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3'), _session('A-4')]}
        onSelect={() => {}}
      />,
    );
    expect(tabOrder(container)).toEqual(['A-3', 'A-1', 'A-2', 'A-4']);
  });

  test('clicking a tab\'s pin button persists + reorders without crashing', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        onSelect={() => {}}
      />,
    );
    // Pin A-3 → it should move to the leftmost slot.
    const pinButtons = container.querySelectorAll('.tab-pin-btn');
    fireEvent.click(pinButtons[2]);
    expect(tabOrder(container)).toEqual(['A-3', 'A-1', 'A-2']);
    // And persisted.
    expect(readPinnedIds(window.localStorage)).toEqual(['A-3']);
  });

  test('pin → unpin returns the tab to its original position', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        onSelect={() => {}}
      />,
    );
    const pinBtnsBefore = container.querySelectorAll('.tab-pin-btn');
    fireEvent.click(pinBtnsBefore[1]); // pin A-2
    expect(tabOrder(container)).toEqual(['A-2', 'A-1', 'A-3']);
    // The A-2 tab is now FIRST — its pin button is at index 0.
    const pinBtnsAfter = container.querySelectorAll('.tab-pin-btn');
    fireEvent.click(pinBtnsAfter[0]); // unpin A-2
    expect(tabOrder(container)).toEqual(['A-1', 'A-2', 'A-3']);
    expect(readPinnedIds(window.localStorage)).toEqual([]);
  });

  test('pinned tab gets is-pinned class so CSS sticky positioning kicks in', () => {
    window.localStorage.setItem(
      PINNED_TABS_STORAGE_KEY, JSON.stringify(['A-2']),
    );
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2')]}
        onSelect={() => {}}
      />,
    );
    const tabs = container.querySelectorAll('li.tab');
    // A-2 is pinned → moved to position 0 → marked .is-pinned.
    expect(tabs[0]).toHaveClass('is-pinned');
    expect(tabs[1]).not.toHaveClass('is-pinned');
  });

  test('stale pinned ids (no matching session) are silently ignored', () => {
    window.localStorage.setItem(
      PINNED_TABS_STORAGE_KEY,
      JSON.stringify(['T-deleted', 'A-2']),
    );
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2')]}
        onSelect={() => {}}
      />,
    );
    // Stale 'T-deleted' is dropped; A-2 still pins to the left.
    expect(tabOrder(container)).toEqual(['A-2', 'A-1']);
  });
});
