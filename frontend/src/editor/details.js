import { Details } from '@tiptap/extension-details'

// Store collapsible blocks as plain HTML, which every Markdown renderer understands:
//
//   <details>
//   <summary>Ruwe aantekeningen</summary>
//
//   ...markdown...
//
//   </details>
const DETAILS_RE = /^<details[^>]*>[ \t]*\n?[ \t]*<summary>([\s\S]*?)<\/summary>[ \t]*\n([\s\S]*?)\n?[ \t]*<\/details>[ \t]*(?:\n+|$)/

export const SchriftDetails = Details.extend({
  markdownTokenizer: {
    name: 'details',
    level: 'block',
    start(src) {
      const m = src.match(/^<details/m)
      return m ? m.index : -1
    },
    tokenize(src, _tokens, lexer) {
      const m = src.match(DETAILS_RE)
      if (!m) return undefined
      return {
        type: 'details',
        raw: m[0],
        summaryTokens: lexer.inlineTokens(m[1].trim()),
        tokens: lexer.blockTokens(m[2].trim()),
      }
    },
  },
  parseMarkdown(token, h) {
    const summary = h.createNode('detailsSummary', {}, h.parseInline(token.summaryTokens || []))
    let blocks = h.parseChildren(token.tokens || [])
    if (!blocks.length) blocks = [h.createNode('paragraph', {}, [])]
    const content = h.createNode('detailsContent', {}, blocks)
    return h.createNode('details', {}, [summary, content])
  },
  renderMarkdown(node, h) {
    const children = node.content || []
    const summary = children.find((c) => c.type === 'detailsSummary')
    const content = children.find((c) => c.type === 'detailsContent')
    const s = summary ? h.renderChildren(summary.content || []) : ''
    const c = content ? h.renderChildren(content.content || [], '\n\n') : ''
    return `<details>\n<summary>${s}</summary>\n\n${c.trim()}\n\n</details>`
  },
})
