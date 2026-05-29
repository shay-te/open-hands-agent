// Global single-flight lock for review-comment submissions.
//
// Without this, an operator with multiple comment forms open
// (one per file, one per line) could fire several submits in
// parallel — and kato runs review-fix runs immediately on
// top-of-thread submits, so two parallel submits = two parallel
// review-fix spawns racing for the same workspace. The local
// per-form ``busy`` flag inside ``CommentForm`` only guards
// double-clicks within ONE form; this store guards across forms.
//
// Same shape as ``toastStore.js`` — plain pub/sub, no React,
// reachable from non-component code.

import { createPubSub } from './pubsub.js';

let _busy = false;

const _pubsub = createPubSub(() => _busy);
const _emit = _pubsub.emit;


export const commentSubmitLock = {
  isBusy() { return _busy; },

  // Try to acquire the lock. Returns true on success, false if
  // someone else already holds it. Caller MUST call ``release``
  // exactly once via try/finally.
  acquire() {
    if (_busy) { return false; }
    _busy = true;
    _emit();
    return true;
  },

  release() {
    if (!_busy) { return; }
    _busy = false;
    _emit();
  },

  subscribe: _pubsub.subscribe,
};
