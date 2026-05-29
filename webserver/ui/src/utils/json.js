// Parse JSON, returning ``fallback`` instead of throwing on missing or
// malformed input. Used for reading JSON blobs out of localStorage where
// the value may be absent, truncated, or hand-corrupted.
//
// Callers keep their own post-parse shape validation (object vs array vs
// plain-object) — this only removes the repeated empty-check + try/catch.
export function parseJsonOr(raw, fallback) {
  if (!raw) { return fallback; }
  try {
    return JSON.parse(raw);
  } catch (_err) {
    return fallback;
  }
}
