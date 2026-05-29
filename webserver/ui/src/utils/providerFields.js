// A provider field is a secret (render as a password input, never echo
// its value back) when its key mentions a token / secret / password.
export function isSecretKey(key) {
  const lower = String(key).toLowerCase();
  return lower.includes('token')
    || lower.includes('secret')
    || lower.includes('password');
}

// Build the editable draft for one provider: ``{ fieldKey: currentValue }``.
export function buildDraftFor(providers, name) {
  const fields = (providers?.[name]?.fields) || {};
  const out = {};
  for (const key of Object.keys(fields)) {
    out[key] = fields[key]?.value || '';
  }
  return out;
}
