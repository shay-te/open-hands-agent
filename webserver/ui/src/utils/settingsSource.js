// Map a settings field's ``source`` (where its value came from) to a
// short badge label, or — for single-value panels that show it inline —
// a verbose human sentence.
export function sourceLabel(source) {
  if (source === 'env') { return 'live'; }
  if (source === 'kato_settings') { return 'saved'; }
  if (source === 'env_file') { return '.env'; }
  return 'unset';
}

export function sourceLabelVerbose(source) {
  if (source === 'env') { return 'Live (process env)'; }
  if (source === 'kato_settings') { return 'Saved (~/.kato/settings.json)'; }
  if (source === 'env_file') { return 'From .env file (legacy fallback)'; }
  return 'Unset';
}
