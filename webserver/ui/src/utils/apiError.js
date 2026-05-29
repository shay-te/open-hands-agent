// The API client resolves to ``{ ok, body, error }``. A human-readable
// error can live on ``body.error`` (a message the server sent back) or
// on ``error`` (a transport/parse failure). Callers want the first one
// that's set, falling back to a context-specific default.
//
// This collapses the ``(result.body && result.body.error) || result.error
// || '<fallback>'`` chain (and its ``result.body?.error`` / ``String(...)``
// spellings) that was hand-written across the modals, diff, settings
// panels, and session header.
export function apiErrorMessage(result, fallback = '') {
  const body = result && result.body;
  return String((body && body.error) || (result && result.error) || fallback);
}
