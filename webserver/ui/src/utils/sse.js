import { parseJsonOr } from './json.js';

// SSE frame payloads are JSON strings; parse defensively, null on garbage.
export function safeParseJSON(text) {
  return parseJsonOr(text, null);
}
