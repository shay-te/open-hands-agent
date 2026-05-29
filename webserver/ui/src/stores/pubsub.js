// Tiny pub/sub primitive shared by the app's hand-rolled stores
// (``toastStore``, ``commentSubmitLock``). Both used to carry an
// identical ``_listeners`` Set + ``_emit`` try/catch loop + a
// fire-once-on-subscribe ``subscribe``. This factory owns exactly
// that boilerplate; each store keeps its own state and public API
// and composes the factory for the listener bookkeeping.
//
// ``getSnapshot`` is the store-supplied "current value" getter. The
// factory calls it to produce the payload handed to every subscriber
// on ``emit()`` and on the initial ``subscribe()`` fire — so each
// store decides what shape its subscribers see (a copied array, a
// boolean, etc.). Every subscriber invocation is wrapped in try/catch
// so one throwing subscriber can never take down ``emit`` or the
// caller of ``subscribe``.
export function createPubSub(getSnapshot) {
  const listeners = new Set();

  function emit() {
    const snapshot = getSnapshot();
    for (const fn of listeners) {
      try { fn(snapshot); } catch (_) { /* never let one subscriber break others */ }
    }
  }

  function subscribe(fn) {
    listeners.add(fn);
    // Fire once immediately so late mounters render the current state.
    // Wrapped in try/catch so a throwing subscriber can't take down
    // the caller of subscribe() — same defense as emit().
    try { fn(getSnapshot()); } catch (_) { /* see emit */ }
    return () => { listeners.delete(fn); };
  }

  return { subscribe, emit };
}
