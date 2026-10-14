import { Extension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

// Task metadata and wikilinks stay plain text in the document (so Markdown round-trips
// untouched); decorations turn them into pills and links on screen.
export const DUE_RE = /\u{1F4C5}\uFE0F?\s*(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}))?/gu
const HIDDEN_RE = /\s?(?:\u{1F194}\uFE0F?\s*[A-Za-z0-9_-]+|\u2705\uFE0F?\s*\d{4}-\d{2}-\d{2})/gu
const WIKI_RE = /\[\[([^[\]|#\n]+)((?:[#|][^[\]\n]*)?)\]\]/g

const MONTHS = {
  nl: ['jan', 'feb', 'mrt', 'apr', 'mei', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'],
  en: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
}
const DAYS = {
  nl: ['zo', 'ma', 'di', 'wo', 'do', 'vr', 'za'],
  en: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
}

export function formatDue(date, time, lang = 'nl') {
  const [y, m, d] = date.split('-').map(Number)
  const day = new Date(y, m - 1, d)
  const thisYear = new Date().getFullYear()
  const l = MONTHS[lang] ? lang : 'nl'
  let label = `${DAYS[l][day.getDay()]} ${d} ${MONTHS[l][m - 1]}`
  if (y !== thisYear) label += ` ${y}`
  if (time) label += `, ${time.padStart(5, '0')}`
  return label
}

function isOverdue(date) {
  const today = new Date()
  const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
  return date < iso
}

function build(doc, lang, selection) {
  const near = (from, to) => selection && selection.from >= from && selection.from <= to
  const decos = []
  doc.descendants((node, pos) => {
    if (!node.isText) return
    const text = node.text
    for (const m of text.matchAll(DUE_RE)) {
      const from = pos + m.index
      const to = from + m[0].length
      if (near(from, to)) continue
      decos.push(
        Decoration.inline(from, to, {
          class: 'due-pill' + (isOverdue(m[1]) ? ' due-late' : ''),
          'data-due': m[1] + (m[2] ? ' ' + m[2] : ''),
          'data-label': formatDue(m[1], m[2], lang),
          'data-from': String(from),
          'data-to': String(to),
        }),
      )
    }
    for (const m of text.matchAll(HIDDEN_RE)) {
      const from = pos + m.index
      const to = from + m[0].length
      if (near(from, to)) continue
      decos.push(Decoration.inline(from, to, { class: 'tok-hidden' }))
    }
    for (const m of text.matchAll(WIKI_RE)) {
      const from = pos + m.index
      const editing = near(from, from + m[0].length)
      if (!editing) decos.push(Decoration.inline(from, from + 2, { class: 'tok-hidden' }))
      decos.push(
        Decoration.inline(from + 2, from + 2 + m[1].length, {
          class: 'wikilink',
          'data-target': m[1].trim(),
        }),
      )
      if (!editing) {
        decos.push(Decoration.inline(from + 2 + m[1].length, from + m[0].length, { class: 'tok-hidden' }))
      }
    }
  })
  return DecorationSet.create(doc, decos)
}

export const Tokens = Extension.create({
  name: 'schriftTokens',
  addOptions() {
    return { lang: 'nl', onDueClick: null, onWikiClick: null }
  },
  addProseMirrorPlugins() {
    const opts = this.options
    return [
      new Plugin({
        key: new PluginKey('schriftTokens'),
        state: {
          init: (_, { doc, selection }) => build(doc, opts.lang, selection),
          apply: (tr, old) =>
            tr.docChanged || tr.selectionSet ? build(tr.doc, opts.lang, tr.selection) : old,
        },
        props: {
          decorations(state) {
            return this.getState(state)
          },
          handleClick(view, pos, event) {
            const el = event.target.closest?.('.due-pill, .wikilink')
            if (!el) return false
            if (el.classList.contains('due-pill') && opts.onDueClick) {
              opts.onDueClick({
                el,
                value: el.dataset.due,
                from: Number(el.dataset.from),
                to: Number(el.dataset.to),
              })
              return true
            }
            if (el.classList.contains('wikilink') && opts.onWikiClick) {
              opts.onWikiClick(el.dataset.target, event)
              return true
            }
            return false
          },
        },
      }),
    ]
  },
})
