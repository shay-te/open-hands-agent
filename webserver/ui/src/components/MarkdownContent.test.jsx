// Tests for MarkdownContent — the renderer that turns Claude's
// text replies into GitHub-flavored markdown with copy buttons on
// fenced code blocks.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

import MarkdownContent from './MarkdownContent.jsx';


describe('MarkdownContent', () => {
  beforeEach(() => {
    // jsdom doesn't ship a clipboard API; stub it per-test so the
    // copy button has something to await without throwing.
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    vi.useFakeTimers();
  });

  test('renders inline markdown formatting (bold, italic, inline code)', () => {
    const { container } = render(
      <MarkdownContent>
        {'Hello **bold** and *italic* and `inline code`.'}
      </MarkdownContent>,
    );
    expect(container.querySelector('strong')).toHaveTextContent('bold');
    expect(container.querySelector('em')).toHaveTextContent('italic');
    expect(container.querySelector('.md-inline-code'))
      .toHaveTextContent('inline code');
  });

  test('renders headings and lists', () => {
    const { container } = render(
      <MarkdownContent>
        {'# Heading\n\n- one\n- two\n- three'}
      </MarkdownContent>,
    );
    expect(container.querySelector('h1')).toHaveTextContent('Heading');
    expect(container.querySelectorAll('li')).toHaveLength(3);
  });

  test('renders GFM tables via remark-gfm', () => {
    const { container } = render(
      <MarkdownContent>
        {'| a | b |\n| - | - |\n| 1 | 2 |'}
      </MarkdownContent>,
    );
    expect(container.querySelector('table')).toBeInTheDocument();
    expect(container.querySelectorAll('th')).toHaveLength(2);
    expect(container.querySelectorAll('td')).toHaveLength(2);
  });

  test('fenced code blocks render with a copy button and the language label', () => {
    const { container } = render(
      <MarkdownContent>
        {'```python\nprint("hi")\n```'}
      </MarkdownContent>,
    );
    const block = container.querySelector('.md-code-block');
    expect(block).toBeInTheDocument();
    expect(container.querySelector('.md-code-block-lang'))
      .toHaveTextContent('python');
    expect(container.querySelector('.md-code-block-copy')).toBeInTheDocument();
    // Code body is preserved verbatim.
    expect(container.querySelector('.md-code-block-code'))
      .toHaveTextContent('print("hi")');
  });

  test('fenced code block without a language falls back to "code" label', () => {
    const { container } = render(
      <MarkdownContent>
        {'```\nplain text\n```'}
      </MarkdownContent>,
    );
    expect(container.querySelector('.md-code-block-lang'))
      .toHaveTextContent('code');
  });

  test('copy button writes the code body to the clipboard', async () => {
    render(
      <MarkdownContent>
        {'```js\nconst x = 1;\n```'}
      </MarkdownContent>,
    );
    const copyBtn = screen.getByLabelText('Copy code');
    await act(async () => { fireEvent.click(copyBtn); });
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('const x = 1;\n');
    // Button flips to the "copied" affordance until the timeout
    // restores the default state.
    expect(screen.getByLabelText('Copied')).toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(1600); });
    expect(screen.getByLabelText('Copy code')).toBeInTheDocument();
  });

  test('inline `code` does NOT get a copy button', () => {
    const { container } = render(
      <MarkdownContent>{'use `npm run build` here'}</MarkdownContent>,
    );
    expect(container.querySelector('.md-code-block')).not.toBeInTheDocument();
    expect(container.querySelector('.md-code-block-copy')).not.toBeInTheDocument();
    expect(container.querySelector('.md-inline-code'))
      .toHaveTextContent('npm run build');
  });

  test('links open in a new tab with safe rel attributes', () => {
    const { container } = render(
      <MarkdownContent>{'[docs](https://example.com)'}</MarkdownContent>,
    );
    const a = container.querySelector('a');
    expect(a).toHaveAttribute('href', 'https://example.com');
    expect(a).toHaveAttribute('target', '_blank');
    expect(a.getAttribute('rel')).toContain('noopener');
    expect(a.getAttribute('rel')).toContain('noreferrer');
  });

  test('non-string children are coerced to a string', () => {
    // Defensive: the call site passes ``textPieces.join('\n')`` so
    // it's always a string, but a future caller could pass a number
    // or other primitive. The component shouldn't crash.
    const { container } = render(
      <MarkdownContent>{42}</MarkdownContent>,
    );
    expect(container.textContent).toContain('42');
  });
});
