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
