import { useEffect, useState } from 'react';
import { unpackPermissionEnvelope } from '../utils/permissionEnvelope.js';

// Approval modal for the agent's tool requests. Stateless about the
// underlying request; presents the input fields, captures rationale +
// "remember this session" toggle, and surfaces the user's decision via
// onDecide(allow, { remember }).
//
// The parent owns the actual round-trip back to the server.
export default function PermissionModal({ raw, onDecide }) {
  const { requestId, toolName, toolInput } = unpackPermissionEnvelope(raw);
  const [rationale, setRationale] = useState('');
  const [remember, setRemember] = useState(false);

  // Reset rationale + remember when the request changes — old text from
  // a prior approval shouldn't bleed into the new one.
  useEffect(() => {
    setRationale('');
    setRemember(false);
  }, [requestId]);

  if (!raw) { return null; }

  const fields = renderFields(toolInput);

  return (
    <div id="permission-modal" className="modal">
      <div className="modal-card">
        <header className="modal-head">
          <h2>Approval requested</h2>
          <span id="permission-tool-name">{toolName}</span>
        </header>
        <div id="permission-fields">{fields}</div>
        <details id="permission-raw" className="modal-raw">
          <summary>raw envelope</summary>
          <pre id="permission-detail">{safeStringify(raw)}</pre>
        </details>
        <label className="modal-remember">
          <input
            type="checkbox"
            id="permission-remember"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
          />
          Don't ask again this session for{' '}
          <code id="permission-remember-tool">{toolName}</code>
        </label>
        <textarea
          id="permission-rationale"
          placeholder="Optional rationale (sent if you Deny)"
          rows={2}
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
        />
        <div className="modal-actions">
          <button
            id="permission-deny"
            type="button"
            className="danger"
            onClick={() => onDecide({ allow: false, rationale, remember, requestId, toolName })}
          >
            Deny
          </button>
          <button
            id="permission-allow"
            type="button"
            className="primary"
            onClick={() => onDecide({ allow: true, rationale, remember, requestId, toolName })}
          >
            Allow
          </button>
        </div>
      </div>
    </div>
  );
}

function renderFields(toolInput) {
  if (!toolInput || typeof toolInput !== 'object'
      || Object.keys(toolInput).length === 0) {
    return (
      <p className="permission-field-value">(no arguments)</p>
    );
  }
  return Object.entries(toolInput).map(([key, value]) => (
    <div className="permission-field" key={key}>
      <span className="permission-field-label">{key}</span>
      <div className="permission-field-value">{formatValue(value)}</div>
    </div>
  ));
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
