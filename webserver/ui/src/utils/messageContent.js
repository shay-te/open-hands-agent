// Shared text extraction for a Claude message envelope's ``content``.
//
// A Claude ``user``/``assistant`` event carries ``raw.message.content``
// as an array of content blocks. This pulls the text out: keep only
// ``{ type: 'text', text }`` blocks (with a truthy ``text``), join them
// with newlines, and trim. Both ``lastPrompt`` (sticky "you asked:"
// bar) and ``MessageFilter._userEventText`` (user-echo dedupe /
// internal-notification filter) used to hand-roll this exact
// filter+map+join+trim. Callers that also accept a string ``content``
// short-circuit that case themselves before calling here.
export function messageContentText(message) {
  const content = Array.isArray(message?.content) ? message.content : [];
  return content
    .filter((b) => b && b.type === 'text' && b.text)
    .map((b) => String(b.text))
    .join('\n')
    .trim();
}
