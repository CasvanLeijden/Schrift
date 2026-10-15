import { Plugin } from '@tiptap/pm/state'
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table'

// Column widths are dragged in the editor and kept in a comment above the table, which
// other Markdown renderers ignore:
//
//   <!-- cols: 160 320 - -->
//   | Pas | Houder | Bevoegdheden |
//
// A `-` is a column without a fixed width.
const COLS_RE = /^<!--[ \t]*cols:[ \t]*([\d -]+?)[ \t]*-->[ \t]*\n/

const base = Table.config

function parseWidths(spec) {
  return spec.split(/\s+/).map((w) => (/^\d+$/.test(w) ? Number(w) : null))
}

function freezeWidthsOnFirstDrag() {
  // A table without widths is laid out by its content. Before the first drag, fix every
  // column at the width it has now, so only the dragged column changes.
  return new Plugin({
    props: {
      handleDOMEvents: {
        mousedown(view, event) {
          if (!view.dom.classList.contains('resize-cursor')) return false
          const tableDom = event.target.closest?.('table')
          if (!tableDom) return false
          const pos = view.posAtDOM(tableDom, 0)
          const $pos = view.state.doc.resolve(pos)
          let depth = $pos.depth
          while (depth > 0 && $pos.node(depth).type.name !== 'table') depth--
          if (depth === 0) return false
          const table = $pos.node(depth)
          const start = $pos.before(depth)
          let fixed = false
          table.descendants((n) => {
            if (n.attrs.colwidth) fixed = true
            return !fixed && n.type.name === 'tableRow'
          })
          if (fixed) return false
          const firstRow = tableDom.querySelector('tr')
          if (!firstRow) return false
          const widths = [...firstRow.children].map((c) => Math.round(c.getBoundingClientRect().width))
          const tr = view.state.tr
          table.forEach((row, rowOffset) => {
            row.forEach((cell, cellOffset, i) => {
              if (widths[i] == null) return
              tr.setNodeMarkup(start + 1 + rowOffset + 1 + cellOffset, null, {
                ...cell.attrs,
                colwidth: [widths[i]],
              })
            })
          })
          view.dispatch(tr)
          return false
        },
      },
    },
  })
}

export const SchriftTable = Table.extend({
  addProseMirrorPlugins() {
    return [freezeWidthsOnFirstDrag(), ...this.parent()]
  },
  markdownTokenizer: {
    name: 'table',
    level: 'block',
    start(src) {
      return COLS_RE.test(src) ? 0 : base.markdownTokenizer.start(src)
    },
    tokenize(src, tokens, helper) {
      const m = src.match(COLS_RE)
      if (!m) return base.markdownTokenizer.tokenize(src, tokens, helper)
      const rest = src.slice(m[0].length)
      // The base tokenizer only takes tables with escaped pipes; marked lexes the rest.
      let token = base.markdownTokenizer.tokenize(rest, tokens, helper)
      if (!token) {
        const lexed = helper.blockTokens(rest.split(/\n[ \t]*\n/)[0])[0]
        if (lexed?.type !== 'table' || !rest.startsWith(lexed.raw)) return undefined
        token = lexed
      }
      return { ...token, raw: m[0] + token.raw, colwidths: parseWidths(m[1]) }
    },
  },
  parseMarkdown(token, h) {
    const node = base.parseMarkdown(token, h)
    const widths = token.colwidths || []
    if (widths.some(Boolean)) {
      for (const row of node.content || []) {
        ;(row.content || []).forEach((cell, i) => {
          if (widths[i]) cell.attrs = { ...(cell.attrs || {}), colwidth: [widths[i]] }
        })
      }
    }
    return node
  },
  renderMarkdown(node, h) {
    const md = base.renderMarkdown(node, h)
    const cells = node.content?.[0]?.content || []
    const widths = cells.map((c) => c.attrs?.colwidth?.[0] || null)
    if (!widths.some(Boolean)) return md
    const comment = `<!-- cols: ${widths.map((w) => w || '-').join(' ')} -->`
    return md.replace(/^\n*/, (lead) => `${lead}${comment}\n`)
  },
})

export const tableExtensions = [
  SchriftTable.configure({ resizable: true, cellMinWidth: 60 }),
  TableRow,
  TableHeader,
  TableCell,
]
