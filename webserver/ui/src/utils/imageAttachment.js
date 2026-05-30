// Helpers for the chat composer's image-attachment flow.
//
// The composer accepts images from three input sources:
//   1. Clipboard paste (onPaste handler reads ``ClipboardEvent.clipboardData.items``)
//   2. Drag-and-drop (onDrop handler reads ``DataTransfer.files``)
//   3. File picker (<input type="file" multiple accept="image/*">)
//
// Each source produces a File / Blob; we convert to base64 with the
// MIME type intact so the backend can build an Anthropic image
// content block. All three paths funnel through ``fileToImagePart``
// so the validation + size cap stay in one place.

// Match Anthropic's accepted image media types. Clipboard paste on
// macOS sometimes hands us ``image/tiff`` (Preview's default) or other
// formats — drop those rather than send something the API will reject.
const ALLOWED_MEDIA_TYPES = new Set([
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
]);

// Conservative per-image cap; matches the backend's 5 MB ceiling.
// Big screenshots (4K, screen recording frames) get rejected client-
// side so the operator sees the toast immediately rather than waiting
// for the round-trip.
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_IMAGES_PER_MESSAGE = 10;

export const IMAGE_REJECT_REASON = {
  UNSUPPORTED_TYPE: 'unsupported_type',
  TOO_LARGE: 'too_large',
  TOO_MANY: 'too_many',
  READ_FAILED: 'read_failed',
};


// Read a Blob/File and produce {media_type, data} where data is the
// pure base64 payload (no `data:` prefix). Returns ``null`` and a
// reason string when the blob is rejected.
export async function fileToImagePart(blob) {
  if (!blob) {
    return { part: null, reason: IMAGE_REJECT_REASON.READ_FAILED };
  }
  const mediaType = (blob.type || '').toLowerCase();
  if (!ALLOWED_MEDIA_TYPES.has(mediaType)) {
    return { part: null, reason: IMAGE_REJECT_REASON.UNSUPPORTED_TYPE };
  }
  if (blob.size > MAX_IMAGE_BYTES) {
    return { part: null, reason: IMAGE_REJECT_REASON.TOO_LARGE };
  }
  const base64 = await readBlobAsBase64(blob);
  if (!base64) {
    return { part: null, reason: IMAGE_REJECT_REASON.READ_FAILED };
  }
  return {
    part: { media_type: mediaType, data: base64 },
    reason: '',
  };
}


// Iterate clipboard items / file lists, convert each, and aggregate
// the successes. Caller surfaces rejections via toast. ``existingCount``
// lets the caller enforce the per-message cap across multiple
// invocations (e.g. paste once, then paste again).
export async function collectImageParts(
  items,
  { existingCount = 0 } = {},
) {
  const parts = [];
  const rejections = [];
  let total = existingCount;
  for (const item of items || []) {
    if (total >= MAX_IMAGES_PER_MESSAGE) {
      rejections.push({ reason: IMAGE_REJECT_REASON.TOO_MANY });
      break;
    }
    const blob = _itemToBlob(item);
    if (!blob) { continue; }
    // eslint-disable-next-line no-await-in-loop
    const { part, reason } = await fileToImagePart(blob);
    if (part) {
      parts.push(part);
      total += 1;
    } else {
      rejections.push({ reason });
    }
  }
  return { parts, rejections };
}


function _itemToBlob(item) {
  // ClipboardEvent items are DataTransferItem; getAsFile() yields a
  // File. DataTransfer files are already File. Plain Blob from a
  // file-picker is also fine.
  if (typeof item.getAsFile === 'function') {
    return item.getAsFile();
  }
  if (item instanceof Blob) {
    return item;
  }
  return null;
}


function readBlobAsBase64(blob) {
  return new Promise(function (resolve) {
    if (typeof FileReader === 'undefined') {
      resolve('');
      return;
    }
    const reader = new FileReader();
    reader.onload = function () {
      const result = reader.result || '';
      // result is "data:<mime>;base64,<payload>"; strip the prefix
      // so we send only the payload to the backend.
      const commaIndex = result.indexOf(',');
      resolve(commaIndex >= 0 ? result.slice(commaIndex + 1) : '');
    };
    reader.onerror = function () { resolve(''); };
    reader.readAsDataURL(blob);
  });
}
