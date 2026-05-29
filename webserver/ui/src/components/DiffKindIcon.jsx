import Icon from './Icon.jsx';

// Maps a diff-file change kind to the FontAwesome icon name used in
// the file tree and diff headers. Shared by FilesTab's tree rows and
// DiffFileWithComments' header so the icon set stays in sync.
export const DIFF_KIND_ICON = {
  add: 'plus',
  delete: 'minus',
  modify: 'edit',
  rename: 'edit',
  copy: 'edit',
};

// Renders the kind glyph inside a ``diff-file-row-kind kind-<kind>``
// span. ``extraClass`` lets callers add the ``tree-row-kind`` modifier
// used on file-tree rows without changing the base chrome.
export default function DiffKindIcon({ kind, extraClass = '' }) {
  const iconName = DIFF_KIND_ICON[kind] || 'edit';
  const className = extraClass
    ? `diff-file-row-kind ${extraClass} kind-${kind || 'modify'}`
    : `diff-file-row-kind kind-${kind || 'modify'}`;
  return (
    <span className={className}>
      <Icon name={iconName} />
    </span>
  );
}
