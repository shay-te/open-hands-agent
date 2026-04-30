// ``control_request`` (modern, from --permission-prompt-tool stdio) nests
// payload under `request`; older ``permission_request`` puts the same
// fields at the top level. Read either uniformly.
export function unpackPermissionEnvelope(raw) {
  const nested = (raw && typeof raw.request === 'object' && raw.request) || {};
  return {
    requestId: String(raw?.request_id || raw?.id || ''),
    toolName: String(
      raw?.tool_name || raw?.tool
      || nested.tool_name || nested.tool || 'tool',
    ),
    toolInput: raw?.input || nested.input || {},
  };
}
