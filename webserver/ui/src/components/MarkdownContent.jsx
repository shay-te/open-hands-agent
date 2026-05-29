import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import Icon from './Icon.jsx';
import { copyTextToClipboard } from '../utils/clipboard.js';

// Renders Claude assistant text as GitHub-flavored markdown.
// Fenced ``` blocks get a copy button; everything else (headings,
// lists, tables, links, inline code, etc.) falls through to the
// default react-markdown renderers.

export default function MarkdownContent({ children }) {
  const source = typeof children === 'string'
    ? children
    : String(children ?? '');
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={MARKDOWN_COMPONENTS}
    >
      {source}
    </ReactMarkdown>
  );
}

// react-markdown v9 dropped the ``inline`` prop on ``code``, so we
// override ``pre`` to catch fenced blocks (which always render as
// <pre><code>…</code></pre>) and let ``code`` handle the inline
// variant on its own.
function InlineCode({ children }) {
  return <code className="md-inline-code">{children}</code>;
}

function PreBlock({ children }) {
  const child = Array.isArray(children) ? children[0] : children;
  const childClassName = child?.props?.className || '';
  const langMatch = /language-([\w-]+)/.exec(childClassName);
  const language = langMatch ? langMatch[1] : '';
  const text = childrenToText(child?.props?.children);
  return <FencedCodeBlock language={language} text={text} />;
}

function FencedCodeBlock({ language, text }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await copyTextToClipboard(text);
      setCopied(true);
      // Revert the icon after a short tick so the operator sees the
      // confirmation without the button getting stuck on "copied".
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API can be blocked (insecure context, denied
      // permission). Stay silent — the operator can still select +
      // copy manually.
    }
  };
  return (
    <div className="md-code-block">
      <div className="md-code-block-header">
        <span className="md-code-block-lang">{language || 'code'}</span>
        <button
          type="button"
          className="md-code-block-copy"
          onClick={onCopy}
          aria-label={copied ? 'Copied' : 'Copy code'}
          data-tooltip={copied ? 'Copied!' : 'Copy code'}
        >
          <Icon name={copied ? 'check' : 'copy'} />
        </button>
      </div>
      <pre className="md-code-block-pre">
        <code className={`md-code-block-code language-${language || 'plain'}`}>
          {text}
        </code>
      </pre>
    </div>
  );
}

function childrenToText(children) {
  if (children == null) { return ''; }
  if (typeof children === 'string') { return children; }
  if (Array.isArray(children)) {
    return children.map(childrenToText).join('');
  }
  if (typeof children === 'object' && children.props) {
    return childrenToText(children.props.children);
  }
  return String(children);
}

// Open links in a new tab — operators jumping out of the chat to a
// docs page shouldn't lose their planning-UI tab.
function MarkdownLink({ href, children }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

const MARKDOWN_COMPONENTS = {
  pre: PreBlock,
  code: InlineCode,
  a: MarkdownLink,
};

export { childrenToText };
