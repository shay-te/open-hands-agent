import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faChevronDown,
  faChevronLeft,
  faChevronRight,
  faChevronUp,
  faFile,
  faFolder,
  faFolderOpen,
  faFolderPlus,
  faPlus,
  faMinus,
  faPen,
  faXmark,
  faArrowsRotate,
  faBell,
  faBellSlash,
  faGear,
  faCircleNotch,
  faTriangleExclamation,
  faCodeCommit,
  faCircle,
  faMagnifyingGlass,
  faArrowUp,
  faArrowDown,
  faCodeMerge,
  faCodePullRequest,
  faCodeCompare,
  faCheck,
  faLink,
  faStop,
  faPlay,
  faPaperPlane,
  faClockRotateLeft,
  faArrowUpRightFromSquare,
  faComment,
  faCopy,
} from '@fortawesome/free-solid-svg-icons';

const ICONS = {
  'chevron-down': faChevronDown,
  'chevron-left': faChevronLeft,
  'chevron-right': faChevronRight,
  'chevron-up': faChevronUp,
  'file': faFile,
  'folder': faFolder,
  'folder-open': faFolderOpen,
  // "Add repository" — distinct from the bare ``plus`` (which the
  // toolbar already uses for "expand all repositories") so the two
  // affordances don't visually collide on multi-repo tasks.
  'folder-plus': faFolderPlus,
  'plus': faPlus,
  'minus': faMinus,
  'edit': faPen,
  'xmark': faXmark,
  'refresh': faArrowsRotate,
  'bell': faBell,
  'bell-slash': faBellSlash,
  'gear': faGear,
  'spinner': faCircleNotch,
  'warning': faTriangleExclamation,
  'commit': faCodeCommit,
  'comment': faComment,
  'dot': faCircle,
  // Action icons used by SessionHeader's round-button row + the
  // chat search capsule. Names follow FontAwesome's free-solid
  // catalogue so future contributors can swap glyphs with one line.
  'search': faMagnifyingGlass,
  'arrow-up': faArrowUp,
  'arrow-down': faArrowDown,
  'merge': faCodeMerge,
  'pull-request': faCodePullRequest,
  // Round "view this file's diff in the centre pane" button on
  // changed file-tree rows.
  'diff': faCodeCompare,
  'check': faCheck,
  'link': faLink,
  'stop': faStop,
  'play': faPlay,
  'send': faPaperPlane,
  'history': faClockRotateLeft,
  // "Open in a new tab" — used by the chat header's open-PR button.
  'external-link': faArrowUpRightFromSquare,
  // Copy-to-clipboard glyph for the markdown code-block copy button.
  'copy': faCopy,
};

export default function Icon({ name, className = '', spin = false }) {
  const def = ICONS[name];
  if (!def) {
    return null;
  }
  return (
    <FontAwesomeIcon icon={def} className={className} spin={spin} />
  );
}
