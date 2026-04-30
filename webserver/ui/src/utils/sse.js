// SSE helper hooks share this JSON-parse-or-null shape.
export function safeParseJSON(text) {
  try { return JSON.parse(text); } catch (_) { return null; }
}
