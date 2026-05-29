import PanelMessage from './PanelMessage.jsx';

// The shared loading / error / ready tri-state every settings panel
// body opens with. Renders the SAME elements the panels used inline:
//   loading        → <PanelMessage>{loadingMessage}</PanelMessage>
//   error          → <PanelMessage error>{error}</PanelMessage>
//   ready (else)   → children (the panel's own body)
// The panel keeps its own header + wrapper div + loading text; only
// this tri-state moves in, so the rendered DOM is byte-identical.
export default function SettingsPanelBody({ loading, error, loadingMessage, children }) {
  if (loading) { return <PanelMessage>{loadingMessage}</PanelMessage>; }
  if (error) { return <PanelMessage error>{error}</PanelMessage>; }
  return children;
}
