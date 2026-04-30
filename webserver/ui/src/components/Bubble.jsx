// One chat-log bubble. `kind` is the visual variant (assistant/user/
// system/tool/error). Rendered as <div> rather than <p> because some
// bodies contain newlines we want preserved via white-space: pre-wrap.
export default function Bubble({ kind, children }) {
  return <div className={`bubble ${kind}`}>{children}</div>;
}
