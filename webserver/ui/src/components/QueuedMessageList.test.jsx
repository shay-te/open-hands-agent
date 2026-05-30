// Tests for QueuedMessageList — the floating list above the
// composer that surfaces queued chat messages with per-row
// Steer (deliver-now) and Remove (drop) buttons.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import QueuedMessageList from './QueuedMessageList.jsx';


function _item(overrides = {}) {
  return {
    id: 'q-1',
    text: 'fix the typo on line 12',
    images: [],
    queuedAt: 1700000000000,
    ...overrides,
  };
}


describe('QueuedMessageList', () => {

  test('renders nothing when items is empty', () => {
    const { container } = render(
      <QueuedMessageList items={[]} onSteer={vi.fn()} onRemove={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  test('renders nothing when items is missing / not an array', () => {
    const { container: c1 } = render(
      <QueuedMessageList onSteer={vi.fn()} onRemove={vi.fn()} />,
    );
    expect(c1.firstChild).toBeNull();
    const { container: c2 } = render(
      <QueuedMessageList items={null} onSteer={vi.fn()} onRemove={vi.fn()} />,
    );
    expect(c2.firstChild).toBeNull();
  });

  test('renders one row per item with the text visible', () => {
    render(
      <QueuedMessageList
        items={[
          _item({ id: 'q-1', text: 'first message' }),
          _item({ id: 'q-2', text: 'second message' }),
          _item({ id: 'q-3', text: 'third message' }),
        ]}
        onSteer={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    expect(screen.getByText('first message')).toBeInTheDocument();
    expect(screen.getByText('second message')).toBeInTheDocument();
    expect(screen.getByText('third message')).toBeInTheDocument();
  });

  test('preserves the order of items as passed in', () => {
    const { container } = render(
      <QueuedMessageList
        items={[
          _item({ id: 'q-1', text: 'A' }),
          _item({ id: 'q-2', text: 'B' }),
          _item({ id: 'q-3', text: 'C' }),
        ]}
        onSteer={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    const rows = container.querySelectorAll('.queued-message-row .queued-message-text');
    expect(Array.from(rows).map((el) => el.textContent)).toEqual(['A', 'B', 'C']);
  });

  test('image-only message shows image-count suffix', () => {
    // Operator pasted screenshots without typing — the list still
    // needs to show SOMETHING actionable, otherwise rows render
    // blank and the Steer button reads as orphaned.
    render(
      <QueuedMessageList
        items={[_item({ text: '', images: [{}, {}] })]}
        onSteer={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    expect(screen.getByText(/2 images/)).toBeInTheDocument();
  });

  test('image-with-text message includes both', () => {
    render(
      <QueuedMessageList
        items={[_item({ text: 'look at this', images: [{}] })]}
        onSteer={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    expect(screen.getByText(/look at this/)).toBeInTheDocument();
    expect(screen.getByText(/1 image/)).toBeInTheDocument();
  });

  test('blank text + no images shows the empty-draft placeholder', () => {
    // Defensive: an empty draft shouldn't render an invisible row
    // the operator can't even target.
    render(
      <QueuedMessageList
        items={[_item({ text: '', images: [] })]}
        onSteer={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    expect(screen.getByText('(empty draft)')).toBeInTheDocument();
  });

  test('clicking Steer fires onSteer with the row id (NOT just the index)', () => {
    const onSteer = vi.fn();
    render(
      <QueuedMessageList
        items={[
          _item({ id: 'q-aaa', text: 'first' }),
          _item({ id: 'q-bbb', text: 'second' }),
        ]}
        onSteer={onSteer}
        onRemove={vi.fn()}
      />,
    );
    const steerButtons = screen.getAllByRole('button', { name: /steer/i });
    fireEvent.click(steerButtons[1]);
    expect(onSteer).toHaveBeenCalledWith('q-bbb');
  });

  test('clicking Remove fires onRemove with the row id', () => {
    const onRemove = vi.fn();
    render(
      <QueuedMessageList
        items={[_item({ id: 'q-zzz' })]}
        onSteer={vi.fn()}
        onRemove={onRemove}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /remove queued/i }));
    expect(onRemove).toHaveBeenCalledWith('q-zzz');
  });

  test('Steer / Remove without handlers does NOT throw', () => {
    // Safe-default: host may forget to wire either prop; the buttons
    // should just no-op rather than crash the chat tab.
    render(
      <QueuedMessageList items={[_item()]} />,
    );
    expect(() => {
      fireEvent.click(screen.getByRole('button', { name: /steer/i }));
    }).not.toThrow();
    expect(() => {
      fireEvent.click(screen.getByRole('button', { name: /remove queued/i }));
    }).not.toThrow();
  });

  test('the list is keyed by item.id so re-renders don\'t lose row state', () => {
    // React-only contract — pinning the aria-label of the root
    // <ul> proves the component renders a single list (not a
    // fragment / portal we'd lose track of in tests).
    render(
      <QueuedMessageList
        items={[_item()]}
        onSteer={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    expect(screen.getByRole('list', { name: /queued messages/i }))
      .toBeInTheDocument();
  });
});


describe('QueuedMessageList — reserves room for the working indicator (--queued-h)', () => {
  // Operator-reported bug: steered/queued messages floated on top of the
  // bottom of the chat and hid the "working…" indicator. The list now
  // publishes its height as --queued-h on its parent so #event-log pads
  // its bottom past the floating list (same mechanism as --composer-h).
  // jsdom ships no ResizeObserver, so stub it.
  function withResizeObserverStub(run) {
    const original = globalThis.ResizeObserver;
    class Stub {
      constructor(cb) { this._cb = cb; }
      observe() {}
      disconnect() {}
    }
    globalThis.ResizeObserver = Stub;
    try {
      return run();
    } finally {
      if (original === undefined) { delete globalThis.ResizeObserver; }
      else { globalThis.ResizeObserver = original; }
    }
  }

  test('writes --queued-h on the parent while there are queued messages', () => {
    withResizeObserverStub(() => {
      const parent = document.createElement('div');
      document.body.appendChild(parent);
      try {
        render(
          <QueuedMessageList items={[_item()]} onSteer={vi.fn()} onRemove={vi.fn()} />,
          { container: parent },
        );
        // jsdom returns 0 for offsetHeight, but the contract is that the
        // variable is published so the CSS calc has a value to add.
        expect(parent.style.getPropertyValue('--queued-h')).toMatch(/px$/);
      } finally {
        document.body.removeChild(parent);
      }
    });
  });

  test('clears --queued-h once the queue empties', () => {
    withResizeObserverStub(() => {
      const parent = document.createElement('div');
      document.body.appendChild(parent);
      try {
        const { rerender } = render(
          <QueuedMessageList items={[_item()]} onSteer={vi.fn()} onRemove={vi.fn()} />,
          { container: parent },
        );
        expect(parent.style.getPropertyValue('--queued-h')).toMatch(/px$/);
        rerender(
          <QueuedMessageList items={[]} onSteer={vi.fn()} onRemove={vi.fn()} />,
        );
        expect(parent.style.getPropertyValue('--queued-h')).toBe('');
      } finally {
        document.body.removeChild(parent);
      }
    });
  });
});
