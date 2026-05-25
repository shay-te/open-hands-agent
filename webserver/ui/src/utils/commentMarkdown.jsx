// React renderer for the pure parser in ``commentMarkdown.js``.
// Everything is React elements (auto-escaped) — no
// dangerouslySetInnerHTML, so a comment body can't inject markup.

import { parseBlocks, tokenizeInline } from './commentMarkdown.js';

function renderInline(text, keyPrefix) {
  return tokenizeInline(text).map((tok, idx) => {
    const key = `${keyPrefix}-${idx}`;
    if (tok.type === 'bold') { return <strong key={key}>{tok.value}</strong>; }
    if (tok.type === 'italic') { return <em key={key}>{tok.value}</em>; }
    if (tok.type === 'code') {
      return <code key={key} className="cmt-md-code">{tok.value}</code>;
    }
    if (tok.type === 'link') {
      return (
        <a key={key} href={tok.href} target="_blank" rel="noreferrer noopener">
          {tok.value}
        </a>
      );
    }
    return tok.value;
  });
}

export function renderCommentMarkdown(body) {
  return parseBlocks(body).map((block, idx) => {
    const key = `b${idx}`;
    if (block.type === 'empty') {
      return <p key={key} className="cmt-md-empty">(empty comment)</p>;
    }
    if (block.type === 'hr') {
      return <hr key={key} className="cmt-md-hr" />;
    }
    if (block.type === 'h') {
      // Render as <h3>-<h6> regardless of the source level so the
      // comment thread's own typography hierarchy stays intact —
      // ``# Headline`` inside a nested comment must not collide
      // with the page's primary headings.
      const level = Math.max(3, Math.min(6, block.level || 3));
      const HTag = `h${level}`;
      return (
        <HTag key={key} className={`cmt-md-h cmt-md-h${level}`}>
          {renderInline(block.value, key)}
        </HTag>
      );
    }
    if (block.type === 'code') {
      return (
        <pre key={key} className="cmt-md-pre"><code>{block.value}</code></pre>
      );
    }
    if (block.type === 'quote') {
      return (
        <blockquote key={key} className="cmt-md-quote">
          {renderInline(block.value, key)}
        </blockquote>
      );
    }
    if (block.type === 'ul' || block.type === 'ol') {
      const ListTag = block.type === 'ul' ? 'ul' : 'ol';
      return (
        <ListTag key={key} className="cmt-md-list">
          {block.items.map((it, j) => (
            <li key={j}>{renderInline(it, `${key}-${j}`)}</li>
          ))}
        </ListTag>
      );
    }
    return (
      <p key={key} className="cmt-md-p">{renderInline(block.value, key)}</p>
    );
  });
}
