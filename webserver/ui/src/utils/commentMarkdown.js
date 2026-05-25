// Pure markdown parser for comment bodies — no React, so node:test
// can cover it. The .jsx sibling maps these tokens to elements.
//
// We deliberately keep this a SMALL subset (what the editor toolbar
// emits): comment bodies double as the prompt kato feeds Claude, so
// they stay plain markdown *text* in storage; this is render-only.

// Inline tokenizer. Order matters: code spans win over emphasis so
// `**x**` inside backticks stays literal.
const WORD = /[\w]/;
export function tokenizeInline(text) {
  const tokens = [];
  const source = String(text || '');
  let rest = source;
  let consumed = 0;
  const patterns = [
    { type: 'code', re: /^`([^`]+)`/ },
    { type: 'bold', re: /^\*\*([^*]+)\*\*/ },
    { type: 'italic-star', re: /^\*([^*]+)\*/ },
    { type: 'italic-underscore', re: /^_([^_]+)_/ },
    { type: 'link', re: /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/ },
  ];
  let buffer = '';
  const flush = () => {
    if (buffer) { tokens.push({ type: 'text', value: buffer }); buffer = ''; }
  };
  while (rest) {
    let matched = false;
    for (const { type, re } of patterns) {
      const m = rest.match(re);
      if (!m) { continue; }
      // CommonMark: `_` does NOT open or close emphasis intra-word, so
      // identifiers like `linked_entity_type` stay literal. `*` keeps
      // the looser rule because it never collides with identifiers.
      if (type === 'italic-underscore') {
        const prevChar = consumed > 0 ? source[consumed - 1] : '';
        const nextChar = source[consumed + m[0].length] || '';
        if (WORD.test(prevChar) || WORD.test(nextChar)) { continue; }
      }
      flush();
      if (type === 'link') {
        tokens.push({ type, value: m[1], href: m[2] });
      } else if (type === 'italic-star' || type === 'italic-underscore') {
        tokens.push({ type: 'italic', value: m[1] });
      } else {
        tokens.push({ type, value: m[1] });
      }
      rest = rest.slice(m[0].length);
      consumed += m[0].length;
      matched = true;
      break;
    }
    if (!matched) {
      buffer += rest[0];
      rest = rest.slice(1);
      consumed += 1;
    }
  }
  flush();
  return tokens;
}

// Block parser → [{ type: 'p'|'code'|'quote'|'ul'|'ol'|'hr'|'h', ... }].
const HR_RE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
const ATX_HEADING_RE = /^\s*(#{1,6})\s+(.+?)\s*#*\s*$/;
export function parseBlocks(body) {
  const text = String(body || '');
  if (!text.trim()) { return [{ type: 'empty' }]; }
  const lines = text.split('\n');
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line.trim())) {
      const code = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i].trim())) {
        code.push(lines[i]); i += 1;
      }
      i += 1;
      blocks.push({ type: 'code', value: code.join('\n') });
      continue;
    }
    // Horizontal rule: ``---``, ``***`` or ``___`` on a line by
    // itself. Claude uses ``---`` as a section break ("Done. […]
    // ---\nAddressed both comments together"); without this it
    // rendered as literal dashes and the body looked like one
    // unbroken wall of text.
    if (HR_RE.test(line)) {
      blocks.push({ type: 'hr' });
      i += 1;
      continue;
    }
    // ATX heading: ``# Title`` through ``###### Title``. Claude leans
    // on these for sub-section labels in long replies; without a
    // dedicated block they fell through to the paragraph branch and
    // rendered as ``# Title`` literal text.
    const atx = line.match(ATX_HEADING_RE);
    if (atx) {
      blocks.push({
        type: 'h',
        level: Math.min(6, atx[1].length),
        value: atx[2],
      });
      i += 1;
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const quote = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quote.push(lines[i].replace(/^\s*>\s?/, '')); i += 1;
      }
      blocks.push({ type: 'quote', value: quote.join(' ') });
      continue;
    }
    const isUl = /^\s*[-*]\s+/.test(line);
    const isOl = /^\s*\d+\.\s+/.test(line);
    if (isUl || isOl) {
      const re = isUl ? /^\s*[-*]\s+/ : /^\s*\d+\.\s+/;
      const items = [];
      while (i < lines.length && re.test(lines[i])) {
        items.push(lines[i].replace(re, '')); i += 1;
      }
      blocks.push({ type: isUl ? 'ul' : 'ol', items });
      continue;
    }
    if (!line.trim()) { i += 1; continue; }
    const para = [];
    while (
      i < lines.length && lines[i].trim()
      && !/^```/.test(lines[i].trim())
      && !HR_RE.test(lines[i])
      && !ATX_HEADING_RE.test(lines[i])
      && !/^\s*>\s?/.test(lines[i])
      && !/^\s*[-*]\s+/.test(lines[i])
      && !/^\s*\d+\.\s+/.test(lines[i])
    ) {
      para.push(lines[i]); i += 1;
    }
    blocks.push({ type: 'p', value: para.join('\n') });
  }
  return blocks;
}
