import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import {
  collectImageParts,
  IMAGE_REJECT_REASON,
} from '../utils/imageAttachment.js';
import { toast } from '../stores/toastStore.js';
import { appendComposerFragment } from '../utils/chatComposerHelpers.js';
import { readDraft, writeDraft } from '../utils/composerDraft.js';

// Composer state (the textarea contents + attached images) lives
// INSIDE this component on purpose — typing should not re-render
// the rest of the UI tree. Earlier the value was lifted to App so
// every keystroke walked the entire tab list, the EventLog, the
// FilesTab tree, and the ChangesTab diff (with comment widgets).
// Multiply that by typing speed and the operator saw visible
// per-keystroke lag on busy tabs.
//
// Now App holds a ref to this component (forwarded via the ref
// arg) and reaches in imperatively when it needs to push a
// fragment ("paste this file path / repo:path snippet into the
// composer"). Typing stays local; appendFragment is rare; both
// paths stay correct without an O(tree) re-render.
//
// Draft text survives tab switches via localStorage keyed by
// ``taskId`` — see ``utils/composerDraft.js`` for the pure helpers.
// SessionDetail keys this component on ``activeTaskId``, so React
// unmounts it when the operator switches tabs and the in-memory
// ``value`` state is dropped. Persisting to localStorage on every
// keystroke lets the next mount (on tab return) read the in-progress
// draft back out — matches VS Code's per-tab draft behaviour.
// Submit / clear / mount-on-empty all wipe the key.
const SINGLE_LINE_TEXTAREA_HEIGHT = 'calc(1.4em + 16px)';

const MessageForm = forwardRef(function MessageForm({
  taskId,
  turnInFlight,
  onSubmit,
  disabled = false,
  disabledReason = '',
  availableModels = [],
  selectedModel = '',
  onModelChange,
  effortLevels = [],
  selectedEffort = '',
  onEffortChange,
}, ref) {
  // Lazy initializer reads the persisted draft once on mount.
  // SessionDetail keys this component on the active task, so this
  // hydrates correctly when the operator tabs back to the task.
  const [value, setValue] = useState(() => readDraft(taskId));
  // Attached images live in component state (not lifted) because the
  // composer is the only thing that reads / writes them — no other
  // pane needs to know what the operator pasted before they hit Send.
  // Attachments are NOT persisted to localStorage (image blobs/data
  // URLs blow up the storage quota); operators redo image attaches
  // on tab return. Text draft is the load-bearing case.
  const [attachments, setAttachments] = useState([]);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef(null);
  const textareaRef = useRef(null);
  const formRef = useRef(null);
  const pendingCaretRef = useRef(null);

  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) { return; }
    if (!String(el.value || '').trim()) {
      el.style.height = SINGLE_LINE_TEXTAREA_HEIGHT;
      return;
    }
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, []);

  // Resize on every value change (typing, draft hydration, fragment paste).
  useEffect(() => { autoResize(); }, [value, autoResize]);

  // Publish the composer's CURRENT rendered height to the parent
  // (#session-detail) as a CSS variable so #event-log can pad its
  // bottom enough to keep the last bubble clear of the floating
  // capsule. Without this the bottom padding is a fixed 120px sized
  // for a single-row composer — multi-paragraph drafts grow the
  // capsule past 120px and the last messages slip behind it.
  //
  // ResizeObserver fires on textarea-auto-grow, attachment add/remove,
  // and viewport reflows. ``--composer-h`` lives on the parent so each
  // session pane owns its own value (no cross-tab leakage).
  useLayoutEffect(() => {
    const form = formRef.current;
    if (!form || typeof ResizeObserver === 'undefined') { return undefined; }
    const target = form.parentElement;
    if (!target) { return undefined; }
    const publishHeight = () => {
      target.style.setProperty('--composer-h', `${form.offsetHeight}px`);
    };
    publishHeight();
    const observer = new ResizeObserver(publishHeight);
    observer.observe(form);
    return () => {
      observer.disconnect();
      target.style.removeProperty('--composer-h');
    };
  }, []);

  useLayoutEffect(() => {
    const caret = pendingCaretRef.current;
    if (caret == null) { return; }
    pendingCaretRef.current = null;
    const el = textareaRef.current;
    if (!el) { return; }
    autoResize();
    try {
      el.focus({ preventScroll: true });
    } catch (_err) {
      el.focus();
    }
    el.setSelectionRange(caret, caret);
    el.scrollTop = el.scrollHeight;
  }, [value, autoResize]);

  // Mirror every text change into localStorage so the next mount
  // (on tab return) hydrates with the same in-progress draft.
  useEffect(() => {
    writeDraft(taskId, value);
  }, [taskId, value]);

  // Expose the imperative API App uses for "paste this fragment"
  // (file-tree clicks, Cmd+P picker results, diff right-click,
  // commit-id paste). Stable per-mount: the parent's
  // ``appendToInput`` callback never changes.
  useImperativeHandle(ref, () => ({
    appendFragment(fragment) {
      setValue((current) => {
        const next = appendComposerFragment(current, fragment);
        pendingCaretRef.current = next.length;
        return next;
      });
    },
    clear() {
      setValue('');
      setAttachments([]);
      writeDraft(taskId, '');
    },
    getValue() { return value; },
  }), [taskId, value]);

  async function submit(event) {
    event.preventDefault();
    if (disabled) { return; }
    const trimmed = (value || '').trim();
    if (!trimmed && attachments.length === 0) { return; }
    // AWAIT onSubmit and only clear local state on a truthy result
    // (or undefined — back-compat with callers that return nothing
    // but never throw). If the send failed, KEEP the draft so the
    // operator can retry — losing the text on a network failure
    // was a real operator pain point.
    let result;
    try {
      result = await onSubmit(trimmed, attachments.map((a) => a.part));
    } catch (_err) {
      // Send threw — caller will have surfaced an error bubble.
      // Preserve the draft + textarea so the operator can retry.
      return;
    }
    // Explicit ``false`` return signals "send failed" without throw;
    // keep the draft. Anything else (including undefined / true) is
    // treated as success.
    if (result === false) { return; }
    setValue('');
    setAttachments([]);
    writeDraft(taskId, '');
  }

  // While Claude is working the composer is in QUEUE mode: the
  // message is held and auto-sent by SessionDetail when the current
  // turn finishes (no mid-turn steering).
  const isQueueing = turnInFlight && !disabled;
  const placeholder = disabled
    ? disabledReason || 'Session is not live — chat resumes when kato re-spawns it.'
    : isQueueing
      ? 'Queue another message… (sends when Claude is free)'
      : 'Reply to Claude';
  const submitClass = isQueueing ? 'is-queued' : '';
  const hasContent = (value || '').trim() || attachments.length > 0;
  const submitLabel = isQueueing ? 'Queue' : 'Send';
  let submitTitle;
  if (disabled) {
    submitTitle = disabledReason || 'Session is not live — chat resumes when kato re-spawns it.';
  } else if (turnInFlight) {
    submitTitle = 'Claude is working — your message will be queued and sent when the turn finishes.';
  } else {
    submitTitle = 'Send your message to Claude (or press Enter).';
  }

  function handleChange(event) {
    setValue(event.target.value);
  }
  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      submit(event);
    }
  }

  async function handlePaste(event) {
    if (disabled) { return; }
    const items = Array.from(event.clipboardData?.items || []);
    const imageItems = items.filter((it) => it.type && it.type.startsWith('image/'));
    if (imageItems.length === 0) { return; }
    // Stop the textarea from inserting a "filename"/blob placeholder
    // when the clipboard has both text and an image.
    event.preventDefault();
    await ingestImages(imageItems);
  }

  async function handleFilePickerChange(event) {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (files.length === 0) { return; }
    await ingestImages(files);
  }

  function handleDragEnter(event) {
    if (disabled) { return; }
    if (!event.dataTransfer || !event.dataTransfer.types) { return; }
    if (Array.from(event.dataTransfer.types).includes('Files')) {
      event.preventDefault();
      setDragging(true);
    }
  }
  function handleDragLeave() { setDragging(false); }
  function handleDragOver(event) {
    if (disabled) { return; }
    if (!event.dataTransfer || !event.dataTransfer.types) { return; }
    if (Array.from(event.dataTransfer.types).includes('Files')) {
      event.preventDefault();
    }
  }
  async function handleDrop(event) {
    if (disabled) { return; }
    event.preventDefault();
    setDragging(false);
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length === 0) { return; }
    await ingestImages(files);
  }

  async function ingestImages(items) {
    const { parts, rejections } = await collectImageParts(items, {
      existingCount: attachments.length,
    });
    if (parts.length > 0) {
      const next = parts.map((part) => ({ part, previewUrl: _previewUrl(part) }));
      setAttachments((prev) => [...prev, ...next]);
    }
    for (const rejection of rejections) {
      toast.show({
        kind: rejection.reason === IMAGE_REJECT_REASON.UNSUPPORTED_TYPE ? 'warning' : 'error',
        title: 'Image attachment rejected',
        message: _rejectionMessage(rejection.reason),
        durationMs: 6000,
      });
    }
  }

  function removeAttachment(index) {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  }

  return (
    <form
      ref={formRef}
      id="message-form"
      onSubmit={submit}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      className={dragging ? 'is-drop-target' : ''}
    >
      {attachments.length > 0 && (
        <div className="message-attachments">
          {attachments.map((attachment, index) => (
            <div key={index} className="message-attachment">
              <img src={attachment.previewUrl} alt="" />
              <button
                type="button"
                className="message-attachment-remove"
                onClick={() => removeAttachment(index)}
                aria-label="Remove attachment"
                title="Remove"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
      <textarea
        ref={textareaRef}
        id="message-input"
        placeholder={placeholder}
        rows={1}
        title="Shift+Enter for newline. Paste or drop images to attach."
        value={value || ''}
        disabled={disabled}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
      />
      <div className="composer-toolbar">
        <div className="composer-toolbar-left">
          <button
            type="button"
            id="message-attach"
            className="tooltip-above"
            data-tooltip="Attach images — paste a screenshot, drop a file, or click to pick."
            disabled={disabled}
            onClick={() => fileInputRef.current?.click()}
            aria-label="Attach images"
          >
            <span aria-hidden="true">+</span>
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            multiple
            style={{ display: 'none' }}
            onChange={handleFilePickerChange}
          />
        </div>
        <div className="composer-toolbar-right">
          {availableModels.length > 0 && (
            <ComposerSelect
              id="model-selector"
              tooltip="Model used for the next session spawn. Takes effect when Claude is re-spawned."
              ariaLabel="Select model"
              value={selectedModel}
              onChange={onModelChange}
            >
              <option value="">Default</option>
              {availableModels.map((m) => (
                <option key={m.id} value={m.id}>{m.label}</option>
              ))}
            </ComposerSelect>
          )}
          {effortLevels.length > 0 && (
            <ComposerSelect
              id="effort-selector"
              tooltip="Reasoning effort for this chat. Higher = more thinking. 'Auto' uses the configured default. A change applies on the next message (the session re-spawns to take effect)."
              ariaLabel="Select reasoning effort"
              value={selectedEffort}
              onChange={onEffortChange}
            >
              <option value="">Effort: Auto</option>
              {effortLevels.map((level) => (
                <option key={level} value={level}>{`Effort: ${level}`}</option>
              ))}
            </ComposerSelect>
          )}
          <button
            type="submit"
            disabled={disabled || !hasContent}
            className={`message-send ${submitClass} tooltip-above`.trim()}
            data-tooltip={submitTitle}
            aria-label={submitLabel}
          >
            <span aria-hidden="true">{isQueueing ? '◴' : '↑'}</span>
          </button>
        </div>
      </div>
    </form>
  );
});


export default MessageForm;


// Shared composer dropdown. The model picker and the effort picker
// are the same control with different options, so they render through
// one component — identical markup, identical ``.composer-select``
// styling (see app.scss). Keep the per-instance ``id`` for tests and
// value targeting; everything visual lives on the shared class.
function ComposerSelect({ id, value, onChange, tooltip, ariaLabel, children }) {
  return (
    <select
      id={id}
      className="composer-select tooltip-above"
      data-tooltip={tooltip}
      value={value}
      onChange={(e) => onChange && onChange(e.target.value)}
      aria-label={ariaLabel}
    >
      {children}
    </select>
  );
}


function _previewUrl(part) {
  // Already-base64; embed directly so React's <img> can render it
  // without having to round-trip through createObjectURL.
  return `data:${part.media_type};base64,${part.data}`;
}


function _rejectionMessage(reason) {
  switch (reason) {
    case IMAGE_REJECT_REASON.UNSUPPORTED_TYPE:
      return 'Only PNG, JPEG, GIF, and WebP are supported.';
    case IMAGE_REJECT_REASON.TOO_LARGE:
      return 'Image is too large (max 5 MB per image).';
    case IMAGE_REJECT_REASON.TOO_MANY:
      return 'Max 10 images per message.';
    case IMAGE_REJECT_REASON.READ_FAILED:
      return 'Could not read the image.';
    default:
      return 'Image rejected.';
  }
}
