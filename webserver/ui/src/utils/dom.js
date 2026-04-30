export function cssEscapeAttr(value) {
  if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
    return CSS.escape(String(value));
  }
  return String(value).replace(/(["\\])/g, '\\$1');
}

export function stringifyShort(obj, max = 120) {
  try {
    const text = JSON.stringify(obj);
    if (!text) { return ''; }
    return text.length > max ? text.slice(0, max - 1) + '…' : text;
  } catch (_) {
    return '';
  }
}
