import { useEffect, useRef } from 'react';
import Bubble from './Bubble.jsx';
import { stringifyShort } from '../utils/dom.js';

// Renders the chat log: every incoming session event becomes one
// (or zero, or a few) bubbles. Auto-scrolls on new content.
export default function EventLog({ events, banner }) {
  const containerRef = useRef(null);
  useEffect(() => {
    const node = containerRef.current;
    if (node) { node.scrollTop = node.scrollHeight; }
  }, [events.length, banner]);

  return (
    <div id="event-log" ref={containerRef}>
      {banner && <Bubble kind="system">{banner}</Bubble>}
      {events.flatMap((event, index) => bubblesFor(event, index))}
    </div>
  );
}

// Map one session event → the bubbles it produces. Returning [] is
// fine for events that shouldn't render (e.g. raw `user` echoes).
function bubblesFor(raw, eventIndex) {
  if (!raw || !raw.type) { return []; }
  switch (raw.type) {
    case 'system':
      if (raw.subtype === 'init') {
        return [
          <Bubble key={keyOf(raw, eventIndex, 'sys')} kind="system">
            {`session_id: ${raw.session_id || '(none yet)'}`}
          </Bubble>,
        ];
      }
      return [];
    case 'assistant':
      return assistantBubbles(raw, eventIndex);
    case 'user':
    case 'stream_event':
      return [];
    case 'result':
      return resultBubbles(raw, eventIndex);
    case 'permission_request':
    case 'control_request':
      // Modal renders the prompt; suppress here so the log isn't noisy.
      return [];
    default:
      return [
        <Bubble key={keyOf(raw, eventIndex, 'tool')} kind="tool">
          {`${raw.type}${raw.subtype ? ' / ' + raw.subtype : ''}`}
        </Bubble>,
      ];
  }
}

function assistantBubbles(raw, eventIndex) {
  const message = raw.message || {};
  const content = Array.isArray(message.content) ? message.content : [];
  const textPieces = [];
  const toolBubbles = [];
  for (const block of content) {
    if (!block || typeof block !== 'object') { continue; }
    if (block.type === 'text' && block.text) {
      textPieces.push(block.text);
    } else if (block.type === 'tool_use') {
      const toolName = block.name || 'tool';
      toolBubbles.push(
        <Bubble key={keyOf(raw, eventIndex, `tool-${block.id || toolBubbles.length}`)} kind="tool">
          {`→ ${toolName}(${stringifyShort(block.input)})`}
        </Bubble>,
      );
    }
  }
  if (textPieces.length === 0) { return toolBubbles; }
  return [
    ...toolBubbles,
    <Bubble key={keyOf(raw, eventIndex, 'assistant')} kind="assistant">
      {textPieces.join('\n')}
    </Bubble>,
  ];
}

function resultBubbles(raw, eventIndex) {
  const ok = !raw.is_error;
  const summary = raw.result || (ok ? 'completed' : 'failed');
  return [
    <Bubble key={keyOf(raw, eventIndex, 'result')} kind={ok ? 'system' : 'error'}>
      {`(result: ${ok ? 'success' : 'error'}) ${summary}`}
    </Bubble>,
  ];
}

function keyOf(raw, eventIndex, slot) {
  return `${eventIndex}:${raw.uuid || raw.session_id || ''}:${slot}`;
}
