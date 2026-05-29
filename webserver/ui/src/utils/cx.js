// Join the truthy class-name parts into one ``className`` string.
//
//   cx('bubble', isActive && 'is-active', kind ? `bubble-${kind}` : '')
//
// Replaces the ``[base, cond ? 'x' : '', ...].filter(Boolean).join(' ')``
// idiom that was hand-written across the chat, header, and tab components.
export function cx(...parts) {
  return parts.filter(Boolean).join(' ');
}
