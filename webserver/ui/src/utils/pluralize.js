// Pluralize a noun by count: ``pluralize(1, 'image')`` → 'image',
// ``pluralize(3, 'image')`` → 'images'. Pass an explicit ``plural`` for
// irregular words.
export function pluralize(count, noun, plural) {
  return Math.abs(Number(count)) === 1 ? noun : (plural || `${noun}s`);
}

// "1 image" / "3 images" — count followed by the pluralized noun.
export function countNoun(count, noun, plural) {
  return `${count} ${pluralize(count, noun, plural)}`;
}

// Append a parenthesised image-count note to a text body, e.g.
// "(3 images attached)". Returns the text unchanged when ``count``
// is zero. ``separator`` sits between the text and the note (only
// when the text is non-empty); ``label`` is an optional trailing
// word inside the parens (pass '' to drop it). Shared by the chat
// transcript (newline + "attached") and the queued-message list
// (" · ", no label) so both render the same note format.
export function withImageCountSuffix(text, count, { separator = '\n', label = 'attached' } = {}) {
  if (!(count > 0)) { return text; }
  const note = label ? `${countNoun(count, 'image')} ${label}` : countNoun(count, 'image');
  return `${text}${text ? separator : ''}(${note})`;
}
