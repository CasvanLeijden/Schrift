import { Extension } from '@tiptap/core'
import { PluginKey } from '@tiptap/pm/state'
import Suggestion from '@tiptap/suggestion'

import { api, tr } from '../app-shared.js'
import { createEditor, getMarkdown } from './setup.js'

const DUE = '\u{1F4C5}'
const SAVE_DELAY = 700
const POLL_EVERY = 15000

// --- small popup list used by the slash menu and [[ autocomplete ----------------------------

function popupRenderer(itemLabel) {
  let el = null
  let items = []
  let active = 0
  let command = null

  function draw() {
    el.innerHTML = ''
    if (!items.length) {
      const empty = document.createElement('div')
      empty.className = 'px-3 py-2 text-muted'
      empty.textContent = tr('Geen notities gevonden')
      el.appendChild(empty)
      return
    }
    items.forEach((item, i) => {
      const b = document.createElement('button')
      b.type = 'button'
      b.className = 'slash-item' + (i === active ? ' is-active' : '')
      const [label, hint] = itemLabel(item)
      b.textContent = label
      if (hint) {
        const small = document.createElement('small')
        small.textContent = hint
        b.appendChild(small)
      }
      b.addEventListener('mousedown', (e) => {
        e.preventDefault()
        command(item)
      })
      el.appendChild(b)
    })
  }

  function place(rect) {
    if (!rect) return
    const r = rect()
    if (!r) return
    const top = Math.min(r.bottom + 6, window.innerHeight - 330)
    el.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - 270))}px`
    el.style.top = `${top}px`
  }

  return {
    onStart(props) {
      el = document.createElement('div')
      el.className = 'slash-menu'
      el.setAttribute('role', 'listbox')
      document.body.appendChild(el)
      items = props.items
      active = 0
      command = props.command
      draw()
      place(props.clientRect)
    },
    onUpdate(props) {
      items = props.items
      active = 0
      command = props.command
      draw()
      place(props.clientRect)
    },
    onKeyDown({ event }) {
      if (!items.length) return false
      if (event.key === 'ArrowDown') {
        active = (active + 1) % items.length
        draw()
        return true
      }
      if (event.key === 'ArrowUp') {
        active = (active - 1 + items.length) % items.length
        draw()
        return true
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        command(items[active])
        return true
      }
      if (event.key === 'Escape') {
        el?.remove()
        el = null
        return true
      }
      return false
    },
    onExit() {
      el?.remove()
      el = null
    },
  }
}

function slashCommands(ctx) {
  return [
    { label: tr('Kop 1'), hint: '#', run: (c) => c.setNode('heading', { level: 1 }) },
    { label: tr('Kop 2'), hint: '##', run: (c) => c.setNode('heading', { level: 2 }) },
    { label: tr('Kop 3'), hint: '###', run: (c) => c.setNode('heading', { level: 3 }) },
    { label: tr('Takenlijst'), hint: '[ ]', run: (c) => c.toggleTaskList() },
    { label: tr('Opsomming'), hint: '-', run: (c) => c.toggleBulletList() },
    { label: tr('Genummerde lijst'), hint: '1.', run: (c) => c.toggleOrderedList() },
    { label: tr('Datum'), hint: DUE, run: (c) => c, after: () => ctx.insertDue() },
    { label: tr('Link naar notitie'), hint: '[[', run: (c) => c.insertContent('[['), keep: true },
    { label: tr('Bestand of afbeelding'), hint: '', run: (c) => c, after: () => ctx.pickFile() },
    { label: tr('Citaat'), hint: '>', run: (c) => c.toggleBlockquote() },
    { label: tr('Code'), hint: '```', run: (c) => c.toggleCodeBlock() },
    { label: tr('Uitklapblok'), hint: '', run: (c) => c.setDetails() },
    {
      label: tr('Tabel'),
      hint: '',
      run: (c) => c.insertTable({ rows: 3, cols: 3, withHeaderRow: true }),
    },
    { label: tr('Scheidingslijn'), hint: '---', run: (c) => c.setHorizontalRule() },
  ]
}

function SlashMenu(ctx) {
  return Extension.create({
    name: 'slashMenu',
    priority: 1000,
    addProseMirrorPlugins() {
      return [
        Suggestion({
          editor: this.editor,
          pluginKey: new PluginKey('slashMenu'),
          char: '/',
          startOfLine: false,
          allowSpaces: false,
          items: ({ query }) => {
            const q = query.toLowerCase()
            return slashCommands(ctx).filter((i) => i.label.toLowerCase().includes(q))
          },
          command: ({ editor, range, props }) => {
            const chain = editor.chain().focus().deleteRange(range)
            props.run(chain).run()
            if (props.after) setTimeout(props.after, 0)
          },
          render: () => popupRenderer((i) => [i.label, i.hint]),
        }),
      ]
    },
  })
}

function WikiSuggest(currentId) {
  return Extension.create({
    name: 'wikiSuggest',
    priority: 1000,
    addProseMirrorPlugins() {
      return [
        Suggestion({
          editor: this.editor,
          pluginKey: new PluginKey('wikiSuggest'),
          char: '[[',
          allowSpaces: true,
          startOfLine: false,
          allow: ({ state, range }) => {
            const text = state.doc.textBetween(range.from, range.to, '\n')
            return !text.slice(2).includes(']') && !text.includes('\n') && text.length < 80
          },
          items: async ({ query }) => {
            const { ok, data } = await api('GET', `/api/titles?q=${encodeURIComponent(query)}`)
            const list = ok ? data.filter((n) => n.id !== currentId) : []
            if (query.trim() && !list.some((n) => n.title.toLowerCase() === query.trim().toLowerCase())) {
              list.push({ id: null, title: query.trim(), path: tr('Nieuwe notitie') })
            }
            return list
          },
          command: ({ editor, range, props }) => {
            editor.chain().focus().deleteRange(range).insertContent(`[[${props.title}]] `).run()
          },
          render: () => popupRenderer((n) => [n.title, n.path?.replace(/\.md$/, '')]),
        }),
      ]
    },
  })
}

// --- the Alpine component for the note page ----------------------------------------------------

function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

window.Alpine.data('noteEditor', () => {
  // The TipTap editor must not live in Alpine's reactive state: proxies break ProseMirror.
  let editor = null
  return {
    note: null,
    title: '',
    tags: [],
    newTag: '',
    hash: null,
    status: 'saved',
    dirty: false,
    saving: false,
    conflict: null,
    menu: false,
    versionsOpen: false,
    versions: [],
    preview: null,
    updatedBy: null,
    updatedAt: null,
    due: { open: false, date: '', time: '', from: 0, to: 0, style: '' },
    table: { open: false, style: '' },

    get statusText() {
      if (this.status === 'saving') return tr('Opslaan…')
      if (this.status === 'error') return tr('Niet opgeslagen')
      return tr('Opgeslagen')
    },
    get changedLabel() {
      if (!this.updatedAt) return ''
      const who = this.updatedBy === 'mcp' ? 'Claude' : this.sourceLabel(this.updatedBy)
      const t = new Date(this.updatedAt)
      const time = t.toLocaleTimeString(window.SCHRIFT.lang, { hour: '2-digit', minute: '2-digit' })
      return `${tr('Gewijzigd door')} ${who}, ${time}`
    },

    init() {
      this.note = JSON.parse(document.getElementById('note-data').textContent)
      this.title = this.note.title
      this.tags = this.note.tags
      this.hash = this.note.hash
      this.updatedBy = this.note.updatedBy
      this.updatedAt = this.note.updatedAt
      if (this.$refs.title) this.$nextTick(() => this.autosize(this.$refs.title))

      editor = createEditor({
        element: this.$refs.editor,
        markdown: this.note.content,
        lang: window.SCHRIFT.lang,
        placeholder: tr('Typ / voor opmaak'),
        extensions: [SlashMenu(this), WikiSuggest(this.note.id)],
        tokens: {
          onDueClick: (info) => this.openDue(info),
          onWikiClick: (target) => {
            window.location.href = `/go?title=${encodeURIComponent(target)}&origin=${this.note.id}`
          },
        },
      })
      window.schriftEditor = editor
      editor.on('update', ({ transaction }) => {
        if (transaction.getMeta('remote')) return
        this.scheduleSave()
      })
      editor.on('selectionUpdate', () => this.placeTableBar())
      editor.on('update', () => this.placeTableBar())
      editor.on('blur', () => setTimeout(() => this.placeTableBar(), 0))
      document.addEventListener('scroll', () => this.table.open && this.placeTableBar(), true)
      // Images that finish loading move the table; keep the bar on it.
      new ResizeObserver(() => this.table.open && this.placeTableBar()).observe(this.$refs.editor)
      editor.setOptions({
        editorProps: {
          handlePaste: (_view, event) => this.handleFiles(event.clipboardData?.files),
          handleDrop: (_view, event) => this.handleFiles(event.dataTransfer?.files),
        },
      })
      if (!this.note.content.trim() && this.$refs.title && this.title === 'Naamloos') {
        this.title = ''
        this.$nextTick(() => this.$refs.title.focus())
      }
      window.addEventListener('beforeunload', (e) => {
        if (this.dirty) {
          this.saveNow()
          e.preventDefault()
        }
      })
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden' && this.dirty) this.saveNow()
        if (document.visibilityState === 'visible') this.poll()
      })
      setInterval(() => this.poll(), POLL_EVERY)
    },

    autosize(el) {
      el.style.height = 'auto'
      el.style.height = el.scrollHeight + 'px'
    },

    fmt(kind) {
      const chain = editor.chain().focus()
      if (kind === 'heading') chain.toggleHeading({ level: 2 }).run()
      else if (kind === 'bold') chain.toggleBold().run()
      else if (kind === 'task') chain.toggleTaskList().run()
      else if (kind === 'link') chain.insertContent('[[').run()
      else if (kind === 'due') this.insertDue()
    },

    focusEditor() {
      editor?.commands.focus('start')
    },

    markdownForDownload() {
      return editor ? `# ${this.title}\n\n${getMarkdown(editor)}` : ''
    },

    // --- saving ---------------------------------------------------------------------------
    scheduleSave() {
      this.dirty = true
      this.status = 'saving'
      clearTimeout(this._timer)
      this._timer = setTimeout(() => this.saveNow(), SAVE_DELAY)
    },

    async saveNow(force = false) {
      clearTimeout(this._timer)
      if (this.saving) {
        this._again = true
        return
      }
      if (!this.dirty && !force) return
      this.saving = true
      this.dirty = false
      const body = {
        title: this.title,
        content: getMarkdown(editor),
        baseHash: this.hash,
        force,
      }
      const { ok, status, data } = await api('PUT', `/api/notes/${this.note.id}`, body)
      this.saving = false
      if (ok) {
        this.hash = data.hash
        this.status = 'saved'
        this.updatedBy = 'web'
        this.updatedAt = new Date().toISOString()
        if (data.renamed) this.refreshSidebarTitle(data.title)
      } else if (status === 409) {
        this.conflict = data
        this.status = 'error'
        this.dirty = true
      } else {
        this.status = 'error'
        this.dirty = true
        window.schriftToast(data?.error || tr('Er ging iets mis.'))
      }
      if (this._again) {
        this._again = false
        if (this.dirty) this.saveNow()
      }
    },

    refreshSidebarTitle(title) {
      const link = document.querySelector(`[data-note-id="${this.note.id}"] .truncate`)
      if (link) link.textContent = title
      document.title = `${title} · Schrift`
    },

    loadTheirs() {
      const c = this.conflict
      this.conflict = null
      this.applyRemote(c)
      this.status = 'saved'
      this.dirty = false
    },

    keepMine() {
      this.conflict = null
      this.dirty = true
      this.saveNow(true)
    },

    applyRemote(data) {
      this.hash = data.hash
      if (data.title && this.$refs.title) {
        this.title = data.title
        this.$nextTick(() => this.autosize(this.$refs.title))
      }
      const { from, to } = editor.state.selection
      editor.commands.setContent(data.content, { contentType: 'markdown', emitUpdate: false })
      const size = editor.state.doc.content.size
      editor.commands.setTextSelection({ from: Math.min(from, size), to: Math.min(to, size) })
      if (data.tags) this.tags = data.tags
      this.updatedBy = data.updatedBy
      this.updatedAt = data.updatedAt || new Date().toISOString()
    },

    async poll() {
      if (this.dirty || this.saving || this.conflict || document.visibilityState !== 'visible') return
      const { ok, data } = await api('GET', `/api/notes/${this.note.id}/meta`)
      if (!ok || data.hash === this.hash) return
      const full = await api('GET', `/api/notes/${this.note.id}`)
      if (!full.ok || this.dirty) return
      this.applyRemote(full.data)
      if (full.data.updatedBy === 'mcp') window.schriftToast(tr('Bijgewerkt door Claude'))
    },

    // --- tags --------------------------------------------------------------------------------
    async saveTags() {
      const { ok, data } = await api('PUT', `/api/notes/${this.note.id}/tags`, { tags: this.tags })
      if (ok) {
        this.tags = data.tags
        this.hash = data.hash
      }
    },
    addTag() {
      const t = this.newTag.trim().replace(/^#/, '')
      this.newTag = ''
      if (t && !this.tags.some((x) => x.toLowerCase() === t.toLowerCase())) {
        this.tags = [...this.tags, t]
        this.saveTags()
      }
    },
    removeTag(tag) {
      this.tags = this.tags.filter((t) => t !== tag)
      this.saveTags()
    },

    // --- files ----------------------------------------------------------------------------------
    pickFile() {
      this.$refs.file.click()
    },
    handleFiles(files) {
      if (!files || !files.length) return false
      this.uploadFiles([...files])
      return true
    },
    async uploadFiles(files) {
      for (const file of files) {
        window.schriftToast(`${tr('Uploaden…')} ${file.name}`)
        const form = new FormData()
        form.append('file', file)
        form.append('noteId', this.note.id)
        const { ok, data } = await api('POST', '/api/attachments', form)
        if (!ok) {
          window.schriftToast(data?.error || tr('Er ging iets mis.'))
          continue
        }
        if (data.isImage) {
          editor.chain().focus().setImage({ src: data.url, alt: data.filename }).run()
        } else {
          editor
            .chain()
            .focus()
            .insertContent([
              { type: 'text', text: data.filename, marks: [{ type: 'link', attrs: { href: data.url } }] },
              { type: 'text', text: ' ' },
            ])
            .run()
        }
      }
    },

    // --- tables --------------------------------------------------------------------------------------
    placeTableBar() {
      const { $from } = editor.state.selection
      let depth = $from.depth
      while (depth > 0 && $from.node(depth).type.name !== 'table') depth--
      let dom = depth && editor.isFocused ? editor.view.nodeDOM($from.before(depth)) : null
      if (dom && dom.nodeType !== 1) dom = dom.parentElement
      // The active table makes room above itself for the bar, so it covers nothing.
      if (this._tableDom && this._tableDom !== dom) this._tableDom.classList.remove('has-table-bar')
      this._tableDom = dom
      if (!dom) {
        this.table.open = false
        return
      }
      dom.classList.add('has-table-bar')
      const rect = dom.getBoundingClientRect()
      const left = Math.max(8, rect.left)
      this.table = { open: true, style: `left:${left}px;top:${rect.top + 2}px` }
    },
    tableCmd(name) {
      editor.chain().focus()[name]().run()
      this.placeTableBar()
    },

    async deleteAttachment(id) {
      if (this.dirty) await this.saveNow()
      const { ok, data } = await api('DELETE', `/api/attachments/${id}`)
      if (!ok) {
        window.schriftToast(data?.error || tr('Er ging iets mis.'))
        return false
      }
      if (data.note && data.note.hash !== this.hash) this.applyRemote(data.note)
      return true
    },

    // --- due dates ---------------------------------------------------------------------------------
    insertDue() {
      const today = isoDate(new Date())
      const { state } = editor
      const $pos = state.selection.$from
      const end = $pos.end()
      const lineText = $pos.parent.textContent
      if (lineText.includes(DUE)) {
        const offset = lineText.indexOf(DUE)
        const start = $pos.start() + offset
        const m = lineText.slice(offset).match(/^\u{1F4C5}\s*\d{4}-\d{2}-\d{2}(?: \d{1,2}:\d{2})?/u)
        this.openDue({ value: m ? m[0].slice(2).trim() : today, from: start, to: start + (m ? m[0].length : 2) })
        return
      }
      const idMatch = lineText.match(/\s\u{1F194}/u)
      const insertAt = idMatch ? $pos.start() + idMatch.index : end
      const text = ` ${DUE} ${today}`
      editor.chain().focus().insertContentAt(insertAt, text).run()
      const from = insertAt + 1
      this.openDue({ value: today, from, to: from + text.length - 1 })
    },

    openDue({ value, from, to }) {
      const [date, time] = (value || '').split(' ')
      const coords = editor.view.coordsAtPos(from)
      const left = Math.max(8, Math.min(coords.left, window.innerWidth - 272))
      const top = Math.min(coords.bottom + 8, window.innerHeight - 240)
      this.due = { open: true, date, time: time || '', from, to, style: `left:${left}px;top:${top}px` }
    },
    quickDue(days) {
      const d = new Date()
      d.setDate(d.getDate() + days)
      this.due.date = isoDate(d)
    },
    applyDue() {
      if (!this.due.date) return this.clearDue()
      const text = `${DUE} ${this.due.date}${this.due.time ? ' ' + this.due.time : ''}`
      editor.chain().focus().insertContentAt({ from: this.due.from, to: this.due.to }, text).run()
      this.due.open = false
    },
    clearDue() {
      const { doc } = editor.state
      let from = this.due.from
      if (from > 0 && doc.textBetween(from - 1, from) === ' ') from -= 1
      editor.chain().focus().deleteRange({ from, to: this.due.to }).run()
      this.due.open = false
    },

    // --- note actions -------------------------------------------------------------------------------
    async trash() {
      if (this.dirty) await this.saveNow()
      const { ok, data } = await api('DELETE', `/api/notes/${this.note.id}`)
      if (!ok) return window.schriftToast(data?.error || tr('Er ging iets mis.'))
      sessionStorage.setItem(
        'schrift:toast',
        JSON.stringify({ message: tr('Naar prullenbak verplaatst'), undo: `/api/notes/${this.note.id}/restore` }),
      )
      window.location.href = '/inbox'
    },
    async moveTo(value) {
      if (this.dirty) await this.saveNow()
      const folderId = value === 'root' ? null : Number(value)
      const { ok, data } = await api('POST', `/api/notes/${this.note.id}/move`, { folderId, position: 0 })
      if (ok) window.location.reload()
      else window.schriftToast(data?.error || tr('Er ging iets mis.'))
    },

    // --- versions -----------------------------------------------------------------------------------
    sourceLabel(source) {
      const labels = {
        nl: { web: 'jou', mcp: 'Claude', caldav: 'Herinneringen', import: 'import', system: 'Schrift' },
        en: { web: 'you', mcp: 'Claude', caldav: 'Reminders', import: 'import', system: 'Schrift' },
      }
      return (labels[window.SCHRIFT.lang] || labels.nl)[source] || source
    },
    formatTime(iso) {
      return new Date(iso).toLocaleString(window.SCHRIFT.lang, {
        day: 'numeric',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
      })
    },
    async openVersions() {
      if (this.dirty) await this.saveNow()
      this.versionsOpen = true
      this.preview = null
      const { ok, data } = await api('GET', `/api/notes/${this.note.id}/versions`)
      if (ok) this.versions = data
    },
    async showVersion(v) {
      const { ok, data } = await api('GET', `/api/notes/${this.note.id}/versions/${v.id}`)
      if (ok) this.preview = data
    },
    async restoreVersion() {
      const { ok } = await api('POST', `/api/notes/${this.note.id}/versions/${this.preview.id}/restore`)
      if (ok) window.location.reload()
    },
  }
})
