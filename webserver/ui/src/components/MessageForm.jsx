import { useState } from 'react';

// Chat input + send button. The button label flips between "Send" and
// "Steer" depending on whether a turn is in flight (Claude accepts
// stdin mid-turn — sending becomes a steering message).
export default function MessageForm({ turnInFlight, onSubmit }) {
  const [text, setText] = useState('');

  function submit(event) {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed) { return; }
    onSubmit(trimmed);
    setText('');
  }

  return (
    <form id="message-form" onSubmit={submit}>
      <textarea
        id="message-input"
        placeholder="Reply to Claude (Shift+Enter for newline)"
        rows={2}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            submit(e);
          }
        }}
      />
      <button
        type="submit"
        title={turnInFlight
          ? 'Claude is working — your message will steer the in-flight turn.'
          : ''}
      >
        {turnInFlight ? 'Steer' : 'Send'}
      </button>
    </form>
  );
}
