// kato task-tag constants for the web client — the mirror of the namespace +
// segment defined in kato_core_lib/data_layers/data/fields.py. The client only
// *displays* the repository tag (the backend builds the real one), so it needs
// just the prefix; reference REPOSITORY_TAG_PREFIX instead of hand-writing
// ``'kato:repo:'`` in JSX. Keep the namespace/segment in sync with Python.

export const KATO_TAG_NAMESPACE = 'kato';
export const REPOSITORY_TAG_SEGMENT = 'repo';
export const REPOSITORY_TAG_PREFIX = `${KATO_TAG_NAMESPACE}:${REPOSITORY_TAG_SEGMENT}:`;
