// Tab dot statuses. Values mirror the server-side WORKSPACE_STATUS_* enum
// in kato/data_layers/service/workspace_manager.py — keep in sync.
//
// `WORKING` and `ATTENTION` are UI-only overlays from live session state.

export const TAB_STATUS = Object.freeze({
  PROVISIONING: 'provisioning',
  ACTIVE: 'active',
  IDLE: 'idle',
  REVIEW: 'review',
  DONE: 'done',
  TERMINATED: 'terminated',
  ERRORED: 'errored',
  WORKING: 'working',
  ATTENTION: 'attention',
});
