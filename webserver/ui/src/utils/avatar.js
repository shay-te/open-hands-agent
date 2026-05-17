// Bitbucket-style identity avatar. Kato only has an author *name*
// (no uploaded photo), so we render a deterministic initials
// monogram: up to two letters on a colour derived from the name so
// the same person is always the same colour across the diff.

export function avatarInitials(name) {
  const cleaned = String(name || '').trim();
  if (!cleaned) { return '?'; }
  const words = cleaned.split(/[\s._-]+/).filter(Boolean);
  if (words.length === 0) { return cleaned.slice(0, 2).toUpperCase(); }
  if (words.length === 1) {
    return words[0].slice(0, 2).toUpperCase();
  }
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

// Stable, well-spread hue from the name (FNV-1a-ish). Saturation and
// lightness are fixed so every monogram reads on the dark theme.
export function avatarColor(name) {
  const text = String(name || '');
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  const hue = Math.abs(hash) % 360;
  return `hsl(${hue} 45% 38%)`;
}
