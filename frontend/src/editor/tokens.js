import { Extension } from '@tiptap/core'
import { Plugin, PluginKey, Selection, TextSelection } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

// Task metadata and wikilinks stay plain text in the document (so Markdown round-trips
// untouched); decorations turn them into pills and links on screen.
export const DUE_RE = /\u{1F4C5}\uFE0F?\s*(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}))?/gu
// Task ids are only for Schrift and AI: always hidden, also while typing on the line.
const ID_RE = /\s?\u{1F194}\uFE0F?\s*[A-Za-z0-9_-]+/gu
const DONE_RE = /\s?\u2705\uFE0F?\s*\d{4}-\d{2}-\d{2}/gu
// Metadata at the end of a task line: done date and id, in any order.
const HAS_ID = /\u{1F194}/u
const TAIL_RE = /(?:\s*(?:\u{1F194}\uFE0F?\s*[A-Za-z0-9_-]+|\u2705\uFE0F?\s*\d{4}-\d{2}-\d{2}))+\s*$/u
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
    for (const m of text.matchAll(ID_RE)) {
      decos.push(Decoration.inline(pos + m.index, pos + m.index + m[0].length, { class: 'tok-hidden' }))
    }
    for (const m of text.matchAll(DONE_RE)) {
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

// --- keeping the caret out of hidden task ids ---------------------------------------------------

function blockText(node) {
  return node.textBetween(0, node.content.size, '', '￼')
}

// The hidden id ranges in the textblock around $pos; `trailing` when nothing visible follows.
function idRanges($pos) {
  const start = $pos.start()
  const text = blockText($pos.parent)
  return [...text.matchAll(ID_RE)].map((m) => {
    const from = start + m.index
    const to = from + m[0].length
    return { from, to, trailing: !text.slice(m.index + m[0].length).trim() }
  })
}

// Where the metadata tail of the textblock around $pos starts, or null.
function tailStart($pos) {
  const m = blockText($pos.parent).match(TAIL_RE)
  return m && m[0].trim() ? $pos.start() + m.index : null
}

// Inserting a block (image, table, paste) at a task line's end splits off the hidden
// tail into a block of its own. Move such a tail back to the line before it.
function reattachTails(state) {
  let tr = null
  const orphans = []
  state.doc.descendants((node, pos) => {
    if (!node.isTextblock) return true
    const text = blockText(node)
    if (HAS_ID.test(text) && !text.replace(TAIL_RE, '').trim()) orphans.push({ node, pos, text })
    return false
  })
  for (const { node, pos, text } of orphans.reverse()) {
    const before = Selection.findFrom(state.doc.resolve(pos), -1, true)
    if (!before || before.$head.pos >= pos) continue
    const prev = before.$head
    if (HAS_ID.test(blockText(prev.parent))) continue
    tr = tr || state.tr
    tr.delete(tr.mapping.map(pos), tr.mapping.map(pos + node.nodeSize))
    tr.insertText(' ' + text.trim(), tr.mapping.map(prev.end()))
  }
  return tr && tr.setMeta('schriftTail', true)
}

function caretOutOfIds(oldState, state) {
  const sel = state.selection
  if (!sel.empty || !(sel instanceof TextSelection) || !sel.$head.parent.isTextblock) return null
  const head = sel.head
  for (const r of idRanges(sel.$head)) {
    const inside = r.trailing ? head > r.from && head <= r.to : head > r.from && head < r.to
    if (!inside) continue
    const cameFromRight = !r.trailing && oldState.selection.head >= r.to
    const target = r.trailing || cameFromRight ? r.from : r.to
    return state.tr.setSelection(TextSelection.create(state.doc, target))
  }
  return null
}

// Enter, Backspace and Delete at a task line's end would split or join the hidden id
// into the wrong line. Lift the tail out first, run the editor's normal key handling,
// then put the tail back at the end of the line it came from.
let pendingTail = null

function withTailLifted(view, event, from) {
  const { state } = view
  const end = state.doc.resolve(from).end()
  pendingTail = { pos: from, text: state.doc.textBetween(from, end) }
  view.dispatch(state.tr.delete(from, end).setMeta('schriftTail', true))
  view.someProp('handleKeyDown', (f) => f !== handleTailKeys && f(view, event))
  // The key changed nothing: put the tail back where it was.
  if (pendingTail) restoreTail(view.state, view.dispatch)
  return true
}

function restoreTail(state, dispatch, tr = state.tr) {
  const { pos, text } = pendingTail
  pendingTail = null
  let $pos = tr.doc.resolve(Math.min(pos, tr.doc.content.size))
  if (!$pos.parent.isTextblock) $pos = Selection.near($pos, -1).$head
  if (!$pos.parent.isTextblock) return tr
  // Metadata of a line that was joined into this one goes away with that line.
  const start = $pos.start()
  const line = blockText($pos.parent)
  const others = [...line.matchAll(ID_RE), ...line.matchAll(DONE_RE)].sort((a, b) => b.index - a.index)
  for (const m of others) tr.delete(start + m.index, start + m.index + m[0].length)
  tr.insertText(text, tr.doc.resolve(start).end())
  tr.setMeta('schriftTail', true)
  if (dispatch) dispatch(tr)
  return tr
}

function handleTailKeys(view, event) {
  if (event.isComposing || event.metaKey || event.ctrlKey || event.altKey) return false
  const sel = view.state.selection
  if (!sel.empty || !(sel instanceof TextSelection)) return false
  const $head = sel.$head
  if (!$head.parent.isTextblock) return false
  const tail = tailStart($head)

  if (event.key === 'ArrowRight' && !event.shiftKey) {
    const r = idRanges($head).find((x) => x.trailing && x.from === sel.head)
    if (!r) return false
    const next = Selection.findFrom(view.state.doc.resolve($head.after()), 1, true)
    if (next) view.dispatch(view.state.tr.setSelection(next))
    return true
  }
  if (event.key === 'Enter' && !event.shiftKey && tail !== null && sel.head === tail) {
    return withTailLifted(view, event, tail)
  }
  if (event.key === 'Delete' && tail !== null && sel.head === tail) {
    return withTailLifted(view, event, tail)
  }
  if (event.key === 'Backspace' && $head.parentOffset === 0) {
    const before = Selection.findFrom(view.state.doc.resolve($head.before()), -1, true)
    if (!before) return false
    const prevTail = tailStart(before.$head)
    if (prevTail === null) return false
    return withTailLifted(view, event, prevTail)
  }
  return false
}

export const Tokens = Extension.create({
  name: 'schriftTokens',
  // Before the list keymaps (Enter, Backspace), after the suggestion menus.
  priority: 999,
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
        appendTransaction(trs, oldState, state) {
          if (trs.some((t) => t.getMeta('schriftTail'))) return null
          if (pendingTail && trs.some((t) => t.docChanged)) return restoreTail(state, null)
          if (trs.some((t) => t.docChanged)) {
            const fixed = reattachTails(state)
            if (fixed) return fixed
          }
          return caretOutOfIds(oldState, state)
        },
        props: {
          decorations(state) {
            return this.getState(state)
          },
          handleKeyDown: handleTailKeys,
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
