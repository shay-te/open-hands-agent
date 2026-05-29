// Last path segment of a posix/windows path, trailing separators
// stripped. Lives in its own CSS-free module so non-React callers
// (FilesTabHelpers under ``node --test``) can use it without dragging
// in ``diffModel.js``'s ``react-diff-view`` stylesheet side-effect.
export function basenameOf(path) {
  if (!path) { return ''; }
  const trimmed = path.replace(/[\\/]+$/, '');
  const idx = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'));
  return idx >= 0 ? trimmed.slice(idx + 1) : trimmed;
}
