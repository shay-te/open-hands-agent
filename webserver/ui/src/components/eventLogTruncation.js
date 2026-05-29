// Pure-JS helpers for the ``ToolDetails`` and ``EventLog``
// truncation rules. Lives outside the JSX file so ``node --test``
// can import it directly (no JSX transformer needed) and so the
// component file stays as close to "render only" as possible.
//
// The same constants are imported by the JSX. Anything that bumps
// these numbers should bump the test expectations in
// ``eventLogTruncation.test.js`` at the same time so we stay
// honest about the bound on rendered DOM nodes.

// Default-collapsed threshold. Tool output up to this many lines
// renders inline without a "show more" toggle.
export const TOOL_DETAILS_COLLAPSE_THRESHOLD = 40;

// Hard ceiling on rendered lines, even with the operator having
// clicked "show full output". A 50K-line ``cat`` or ``find`` would
// otherwise emit 50K spans into the bubble and freeze the tab.
export const TOOL_DETAILS_HARD_CAP = 1000;

// Default window for ``EventLog`` itself. Long sessions accumulate
// thousands of bubbles; we render only the tail by default. The
// stream cache still has the whole history, so "show older" is a
// state flip, not a refetch.
export const EVENT_LOG_WINDOW_SIZE = 200;

export function computeToolDetailsRender(lines, expanded) {
  if (!expanded && lines.length > TOOL_DETAILS_COLLAPSE_THRESHOLD) {
    return {
      visible: lines.slice(0, TOOL_DETAILS_COLLAPSE_THRESHOLD),
      overflowed: false,
    };
  }
  if (lines.length > TOOL_DETAILS_HARD_CAP) {
    return {
      visible: lines.slice(0, TOOL_DETAILS_HARD_CAP),
      overflowed: true,
    };
  }
  return { visible: lines, overflowed: false };
}

export function computeEventLogWindow(entries, showAll) {
  if (showAll || entries.length <= EVENT_LOG_WINDOW_SIZE) {
    return { visible: entries, hidden: 0 };
  }
  return {
    visible: entries.slice(-EVENT_LOG_WINDOW_SIZE),
    hidden: entries.length - EVENT_LOG_WINDOW_SIZE,
  };
}
