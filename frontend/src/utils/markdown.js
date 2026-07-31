import DOMPurify from 'dompurify'

/**
 * Minimal Markdown renderer for report sections and chat replies.
 *
 * Hand-rolled because it only has to cover what the report agent emits:
 * headings, lists, quotes, bold, code and pipe tables. Swap for `marked` if it
 * ever needs footnotes, reference links or nested block quotes.
 *
 * The input is NOT trusted. Report text is derived from harvested public
 * discussion (Reddit, HN, RSS), so a crafted post can carry markup all the way
 * to the `v-html` bindings that render this output. Two defences, in order:
 *
 *   1. escapeHtml() below neutralises markup in the source before any regex
 *      runs, so `<img onerror=...>` becomes text rather than an element.
 *   2. DOMPurify.sanitize() on the way out, as defence in depth - it catches
 *      anything the generated markup itself gets wrong.
 *
 * Removing either one reintroduces stored XSS.
 */

// Escapes the five characters that can open a tag or break out of an attribute.
// Runs first, so every later regex sees inert text.
const escapeHtml = (text) => text
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#39;')

export const renderMarkdown = (content) => {
  if (!content) return ''

  // Drop a leading ## heading: the section title is already shown outside
  let processedContent = escapeHtml(content).replace(/^##\s+.+\n+/, '')

  // Code blocks
  let html = processedContent.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="code-block"><code>$2</code></pre>')

  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>')

  // Headings
  html = html.replace(/^#### (.+)$/gm, '<h5 class="md-h5">$1</h5>')
  html = html.replace(/^### (.+)$/gm, '<h4 class="md-h4">$1</h4>')
  html = html.replace(/^## (.+)$/gm, '<h3 class="md-h3">$1</h3>')
  html = html.replace(/^# (.+)$/gm, '<h2 class="md-h2">$1</h2>')

  // Blockquotes. Matches the escaped marker: escapeHtml() has already turned a
  // leading "> " into "&gt; " by the time this runs.
  html = html.replace(/^&gt; (.+)$/gm, '<blockquote class="md-quote">$1</blockquote>')

  // Pipe tables. Runs before the list and paragraph passes, which would
  // otherwise chew up the rows. Only the GFM shape is supported - a header row,
  // a |---|---| separator, then body rows - because that is all the report
  // agent is asked to emit. Alignment colons are accepted and ignored.
  html = html.replace(
    /^\|(.+)\|[ \t]*\n\|[ \t]*:?-{1,}:?[ \t]*(?:\|[ \t]*:?-{1,}:?[ \t]*)*\|[ \t]*\n((?:\|.*\|[ \t]*\n?)*)/gm,
    (match, headerRow, bodyRows) => {
      const cells = (row) => row.replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
      const head = cells(headerRow).map((c) => `<th>${c}</th>`).join('')
      const body = bodyRows
        .split('\n')
        .filter((row) => row.trim())
        .map((row) => `<tr>${cells(row).map((c) => `<td>${c}</td>`).join('')}</tr>`)
        .join('')
      return `<table class="md-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>\n`
    }
  )

  // Lists, nested ones included
  html = html.replace(/^(\s*)- (.+)$/gm, (match, indent, text) => {
    const level = Math.floor(indent.length / 2)
    return `<li class="md-li" data-level="${level}">${text}</li>`
  })
  html = html.replace(/^(\s*)(\d+)\. (.+)$/gm, (match, indent, num, text) => {
    const level = Math.floor(indent.length / 2)
    return `<li class="md-oli" data-level="${level}">${text}</li>`
  })

  // Wrap unordered lists
  html = html.replace(/(<li class="md-li"[^>]*>.*?<\/li>\s*)+/g, '<ul class="md-ul">$&</ul>')
  // Wrap ordered lists
  html = html.replace(/(<li class="md-oli"[^>]*>.*?<\/li>\s*)+/g, '<ol class="md-ol">$&</ol>')

  // Strip all whitespace between list items
  html = html.replace(/<\/li>\s+<li/g, '</li><li')
  // Strip whitespace after a list open tag
  html = html.replace(/<ul class="md-ul">\s+/g, '<ul class="md-ul">')
  html = html.replace(/<ol class="md-ol">\s+/g, '<ol class="md-ol">')
  // Strip whitespace before a list close tag
  html = html.replace(/\s+<\/ul>/g, '</ul>')
  html = html.replace(/\s+<\/ol>/g, '</ol>')

  // Bold and italic
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')
  html = html.replace(/_(.+?)_/g, '<em>$1</em>')

  // Horizontal rules
  html = html.replace(/^---$/gm, '<hr class="md-hr">')

  // Line breaks: a blank line splits paragraphs, a single newline becomes <br>
  html = html.replace(/\n\n/g, '</p><p class="md-p">')
  html = html.replace(/\n/g, '<br>')

  // Wrap in paragraphs
  html = '<p class="md-p">' + html + '</p>'

  // Drop empty paragraphs
  html = html.replace(/<p class="md-p"><\/p>/g, '')
  html = html.replace(/<p class="md-p">(<h[2-5])/g, '$1')
  html = html.replace(/(<\/h[2-5]>)<\/p>/g, '$1')
  html = html.replace(/<p class="md-p">(<ul|<ol|<blockquote|<pre|<hr|<table)/g, '$1')
  html = html.replace(/(<\/ul>|<\/ol>|<\/blockquote>|<\/pre>|<\/table>)<\/p>/g, '$1')
  // Strip <br> around block-level elements
  html = html.replace(/<br>\s*(<ul|<ol|<blockquote|<table)/g, '$1')
  html = html.replace(/(<\/ul>|<\/ol>|<\/blockquote>|<\/table>)\s*<br>/g, '$1')
  // Strip <p><br> immediately before a block element, left by a stray blank line
  html = html.replace(/<p class="md-p">(<br>\s*)+(<ul|<ol|<blockquote|<pre|<hr|<table)/g, '$2')
  // Collapse runs of <br>
  html = html.replace(/(<br>\s*){2,}/g, '<br>')
  // Strip the <br> before a paragraph that follows a block element
  html = html.replace(/(<\/ol>|<\/ul>|<\/blockquote>)<br>(<p|<div)/g, '$1$2')

  // Keep ordered-list numbering running when single-item <ol>s are split by paragraphs
  const tokens = html.split(/(<ol class="md-ol">(?:<li class="md-oli"[^>]*>[\s\S]*?<\/li>)+<\/ol>)/g)
  let olCounter = 0
  let inSequence = false
  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i].startsWith('<ol class="md-ol">')) {
      const liCount = (tokens[i].match(/<li class="md-oli"/g) || []).length
      if (liCount === 1) {
        olCounter++
        if (olCounter > 1) {
          tokens[i] = tokens[i].replace('<ol class="md-ol">', `<ol class="md-ol" start="${olCounter}">`)
        }
        inSequence = true
      } else {
        olCounter = 0
        inSequence = false
      }
    } else if (inSequence) {
      if (/<h[2-5]/.test(tokens[i])) {
        olCounter = 0
        inSequence = false
      }
    }
  }
  html = tokens.join('')

  // Defence in depth - see the note at the top of this file. The allowlist is
  // exactly the tags and attributes generated above; anything else is dropped.
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: [
      'p', 'br', 'hr', 'strong', 'em', 'code', 'pre',
      'h2', 'h3', 'h4', 'h5', 'ul', 'ol', 'li', 'blockquote',
      'table', 'thead', 'tbody', 'tr', 'th', 'td',
    ],
    ALLOWED_ATTR: ['class', 'data-level', 'start'],
  })
}

/**
 * Split a section body into its opening verdict and the rest.
 *
 * The report agent is required to open every section with a one-sentence
 * verdict, which the backend normalises to a fully bold first line (see
 * `ReportAgent._normalize_verdict`). The pane shows that line always and keeps
 * the body behind a toggle, so the reader can scan four verdicts before
 * deciding what to open.
 *
 * Returns `{ verdict: '', body: content }` when the first line is not bold,
 * which is what an older report or a model that ignored the format produces.
 */
export const splitVerdict = (content) => {
  if (!content) return { verdict: '', body: '' }

  // The section title is rendered outside the body, as in renderMarkdown().
  const stripped = content.replace(/^##\s+.+\n+/, '')
  const lines = stripped.split('\n')
  const firstIdx = lines.findIndex((line) => line.trim())
  if (firstIdx === -1) return { verdict: '', body: '' }

  const first = lines[firstIdx].trim()
  const bold = first.match(/^\*\*(.+)\*\*$/)
  // A line with inner ** would be two bold runs, not one verdict.
  if (!bold || bold[1].includes('**')) return { verdict: '', body: stripped }

  return {
    verdict: bold[1].trim(),
    body: lines.slice(firstIdx + 1).join('\n').replace(/^\n+/, '')
  }
}

export default renderMarkdown
