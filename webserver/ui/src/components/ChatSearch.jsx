import { useEffect, useRef, useState } from 'react';
import Icon from './Icon.jsx';
import { useEscapeKey } from '../hooks/useEscapeKey.js';

/**
 * Glass capsule search bar that floats over the top of the chat,
 * mirroring the composer at the bottom.
 *
 * State model:
 *   * Closed (default) → renders just a round magnifier icon button
 *     in the toolbar slot. No bar visible, no chat dimming.
 *   * Open → expands into a full glass capsule with an input, a
 *     match counter ("X / N"), prev/next navigation, and a close
 *     button. Live ``query`` mirrored upward via ``onQueryChange``
 *     so :class:`EventLog` can highlight matches in the DOM.
 *
 * ``matchCount`` is reported back by EventLog after each render
 * pass; ``currentMatchIndex`` is the active match within the run
 * (0-based, displayed as 1-based). Prev/next clamp + wrap around
 * the ends so the operator can keep stepping without thinking
 * about the boundary.
 *
 * Closing the bar (× / Esc) clears the query so any highlights
 * vanish from the chat — re-opening starts fresh.
 */
export default function ChatSearch({
  query,
  onQueryChange,
  matchCount = 0,
  currentMatchIndex = 0,
  onPrevMatch,
  onNextMatch,
}) {
  const [open, setOpen] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    if (open && inputRef.current) {
      inputRef.current.focus();
    }
  }, [open]);

  // Keyboard glue: Esc closes (shared hook); Enter/Shift+Enter
  // navigates while the input is focused.
  useEscapeKey(close, open);
  useEffect(() => {
    if (!open) { return undefined; }
    function onKeyDown(event) {
      if (event.key === 'Enter' && event.target === inputRef.current) {
        event.preventDefault();
        if (event.shiftKey) {
          onPrevMatch && onPrevMatch();
        } else {
          onNextMatch && onNextMatch();
        }
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, onPrevMatch, onNextMatch]);

  function close() {
    setOpen(false);
    if (query) {
      onQueryChange('');
    }
  }

  function handleChange(event) {
    onQueryChange(event.target.value);
  }

  const trimmed = (query || '').trim();
  const hasQuery = trimmed.length > 0;
  const hasMatches = hasQuery && matchCount > 0;
  const counter = !hasQuery
    ? ''
    : matchCount === 0
      ? 'no matches'
      : `${currentMatchIndex + 1} / ${matchCount}`;

  // The toggle stays mounted in the header at all times — when the
  // search bar is open we just disable it (rather than yanking it
  // out of the toolbar row, which made the surrounding action
  // buttons reflow and was visually jarring). The bar itself
  // floats absolutely below the header, so rendering both at once
  // doesn't double-occupy the toolbar slot.
  return (
    <>
      <button
        type="button"
        className="chat-search-toggle tooltip-below"
        data-tooltip={open ? 'Search bar is open' : 'Search the chat'}
        onClick={() => setOpen(true)}
        disabled={open}
        aria-label="Search chat"
        aria-pressed={open}
      >
        <Icon name="search" />
      </button>
      {open && (
        <div className="chat-search">
          <span className="chat-search-icon" aria-hidden="true">
            <Icon name="search" />
          </span>
          <input
            ref={inputRef}
            type="text"
            className="chat-search-input"
            placeholder="Search messages…"
            value={query || ''}
            onChange={handleChange}
            aria-label="Search chat messages"
          />
          {counter && (
            <span
              className={`chat-search-count ${hasQuery && matchCount === 0 ? 'is-empty' : ''}`}
              aria-live="polite"
            >
              {counter}
            </span>
          )}
          <button
            type="button"
            className="chat-search-nav tooltip-below"
            data-tooltip="Previous match (Shift+Enter)"
            onClick={() => onPrevMatch && onPrevMatch()}
            disabled={!hasMatches}
            aria-label="Previous match"
          >
            <Icon name="chevron-up" />
          </button>
          <button
            type="button"
            className="chat-search-nav tooltip-below"
            data-tooltip="Next match (Enter)"
            onClick={() => onNextMatch && onNextMatch()}
            disabled={!hasMatches}
            aria-label="Next match"
          >
            <Icon name="chevron-down" />
          </button>
          <button
            type="button"
            className="chat-search-close tooltip-below"
            data-tooltip="Close search (Esc)"
            onClick={close}
            aria-label="Close search"
          >
            <Icon name="xmark" />
          </button>
        </div>
      )}
    </>
  );
}
