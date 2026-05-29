import { useEffect, useState } from 'react';
import { unpackPermissionEnvelope } from '../utils/permissionEnvelope.js';
import DialogShell from './DialogShell.jsx';

export default function PermissionModal({ raw, onDecide }) {
  const { requestId, toolName, toolInput } = unpackPermissionEnvelope(raw);
  const [rationale, setRationale] = useState('');

  useEffect(() => { setRationale(''); }, [requestId]);

  if (!raw) { return null; }

  const fields = renderFields(toolInput);
  const denyTooltip = `Deny this ${toolName} request. Claude will see your rationale (if any) and decide what to do next.`;
  const allowOnceTitle = `Approve this ${toolName} request only — kato will ask again next time.`;
  const allowAlwaysTitle = `Approve and remember ${toolName} — kato won't ask again, even after a kato or browser restart, until you clear it from settings.`;
  function handleRationaleChange(event) {
    setRationale(event.target.value);
  }
  function handleDeny() {
    onDecide({ allow: false, rationale, remember: false, requestId, toolName });
  }
  function handleAllowOnce() {
    onDecide({ allow: true, rationale, remember: false, requestId, toolName });
  }
  function handleAllowAlways() {
    onDecide({ allow: true, rationale, remember: true, requestId, toolName });
  }

  return (
    <DialogShell
      id="permission-modal"
      ariaLabelledBy="permission-modal-title"
      title="Approval requested"
      subtitle={toolName}
      subtitleId="permission-tool-name"
    >
      <div id="permission-fields">{fields}</div>
      <details id="permission-raw" className="modal-raw">
        <summary>raw envelope</summary>
        <pre id="permission-detail">{safeStringify(raw)}</pre>
      </details>
      <textarea
        id="permission-rationale"
        placeholder="Optional rationale (sent if you Deny)"
        rows={2}
        value={rationale}
        onChange={handleRationaleChange}
      />
      <div className="modal-actions">
        <button
          id="permission-deny"
          type="button"
          className="danger tooltip-above"
          data-tooltip={denyTooltip}
          onClick={handleDeny}
        >
          Deny
        </button>
        <button
          id="permission-allow-once"
          type="button"
          className="secondary tooltip-above"
          data-tooltip={allowOnceTitle}
          onClick={handleAllowOnce}
        >
          Allow once
        </button>
        <button
          id="permission-allow-always"
          type="button"
          className="primary tooltip-above"
          data-tooltip={allowAlwaysTitle}
          onClick={handleAllowAlways}
        >
          Allow always
        </button>
      </div>
    </DialogShell>
  );
}

function renderFields(toolInput) {
  const isEmpty = !toolInput
    || typeof toolInput !== 'object'
    || Object.keys(toolInput).length === 0;
  if (isEmpty) {
    return (
      <p className="permission-field-value">(no arguments)</p>
    );
  }
  return Object.entries(toolInput).map(([key, value]) => {
    const formatted = formatValue(value);
    return (
      <div className="permission-field" key={key}>
        <span className="permission-field-label">{key}</span>
        <div className="permission-field-value">{formatted}</div>
      </div>
    );
  });
}

function formatValue(value) {
  if (value == null) { return ''; }
  if (typeof value === 'string') { return value; }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return safeStringify(value);
}

function safeStringify(value) {
  try { return JSON.stringify(value, null, 2); }
  catch (_) { return String(value); }
}
