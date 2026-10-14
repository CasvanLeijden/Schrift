import Alpine from 'alpinejs'
import Sortable from 'sortablejs'

import { api, S, tr } from './app-shared.js'

function toast(message, action) {
  window.dispatchEvent(new CustomEvent('schrift:toast', { detail: { message, action } }))
}
window.schriftToast = toast

Alpine.data('shell', () => ({
  drawer: false,
  addingRootFolder: false,
  toast: null,
  currentFolder: S().currentFolder ?? null,
  init() {
    window.addEventListener('schrift:toast', (e) => {
      this.toast = e.detail
      clearTimeout(this._toastTimer)
      this._toastTimer = setTimeout(() => (this.toast = null), 7000)
    })
    initTreeSorting()
    try {
      const pending = JSON.parse(sessionStorage.getItem('schrift:toast') || 'null')
      sessionStorage.removeItem('schrift:toast')
      if (pending) {
        const action = pending.undo
          ? { label: tr('Ongedaan maken'), run: () => this.undo({ url: pending.undo }) }
          : null
        toast(pending.message, action)
      }
    } catch {
      /* storage unavailable */
    }
  },
  hotkeys(e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault()
      this.drawer = true
      document.getElementById('quick-search')?.focus()
    }
  },
  async newNote(folderId) {
    const { ok, data } = await api('POST', '/api/notes', { title: '', folderId })
    if (ok) window.location.href = data.url
    else toast(data?.error || tr('Er ging iets mis.'))
  },
  async createFolder(parentId, name) {
    if (!name || !name.trim()) return
    const { ok, data } = await api('POST', '/api/folders', { name, parentId })
    if (ok) window.location.reload()
    else toast(data?.error || tr('Er ging iets mis.'))
  },
  toggleTheme() {
    const root = document.documentElement
    const current =
      root.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    const next = current === 'dark' ? 'light' : 'dark'
    root.dataset.theme = next
    const form = new FormData()
    form.append('theme', next)
    fetch('/settings/theme', { method: 'POST', body: form })
  },
  async undo(spec) {
    const { ok, data } = await api('POST', spec.url)
    if (ok && data?.url) window.location.href = data.url
  },
}))

Alpine.data('folderItem', (id, open) => ({
  id,
  open,
  menu: false,
  renaming: false,
  addingSub: false,
  confirming: false,
  toggle() {
    this.open = !this.open
    api('POST', '/api/ui/expanded', { folderId: this.id, open: this.open })
  },
  async rename(name) {
    this.renaming = false
    if (!name.trim()) return
    const { ok, data } = await api('PATCH', `/api/folders/${this.id}`, { name })
    if (ok) window.location.reload()
    else toast(data?.error || tr('Er ging iets mis.'))
  },
  async remove() {
    const { ok, data } = await api('DELETE', `/api/folders/${this.id}`)
    if (ok) window.location.reload()
    else toast(data?.error || tr('Er ging iets mis.'))
  },
}))

Alpine.data('quickSearch', () => ({
  q: '',
  results: [],
  open: false,
  active: -1,
  loading: false,
  async run() {
    const q = this.q.trim()
    if (!q) {
      this.results = []
      this.open = false
      return
    }
    this.loading = true
    const { ok, data } = await api('GET', `/api/search?q=${encodeURIComponent(q)}`)
    this.loading = false
    if (ok && this.q.trim() === q) {
      this.results = data
      this.active = data.length ? 0 : -1
      this.open = true
    }
  },
  move(delta) {
    if (!this.results.length) return
    this.active = (this.active + delta + this.results.length) % this.results.length
  },
  go(e) {
    if (this.open && this.active >= 0 && this.results[this.active]) {
      e.preventDefault()
      window.location.href = '/n/' + this.results[this.active].id
    }
  },
}))

Alpine.data('taskList', () => ({
  done: {},
  async toggle(shortId) {
    const next = !this.done[shortId]
    this.done[shortId] = next
    const { ok, data } = await api('PATCH', `/api/tasks/${shortId}`, { done: next })
    if (!ok) {
      this.done[shortId] = !next
      toast(data?.error || tr('Er ging iets mis.'))
    }
  },
}))

// While dragging, hovering over a collapsed folder opens it, so you can drop into it.
function expandOnHover() {
  let timer = null
  let row = null
  document.addEventListener('dragover', (e) => {
    const target = e.target.closest?.('.tree-folder > .tree-row')
    if (target === row) return
    clearTimeout(timer)
    row = target
    if (!row) return
    timer = setTimeout(() => {
      const data = Alpine.$data(row.parentElement)
      if (data && !data.open) data.toggle()
    }, 500)
  })
  document.addEventListener('dragend', () => {
    clearTimeout(timer)
    row = null
  })
}

function initTreeSorting() {
  expandOnHover()
  const common = {
    animation: 150,
    delay: 180,
    delayOnTouchOnly: true,
    fallbackOnBody: true,
    swapThreshold: 0.6,
    emptyInsertThreshold: 8,
  }
  document.querySelectorAll('[data-note-list]').forEach((el) => {
    Sortable.create(el, {
      ...common,
      group: 'notes',
      draggable: '.tree-note',
      async onEnd(evt) {
        const noteId = evt.item.dataset.noteId
        const folderId = evt.to.dataset.folder ? Number(evt.to.dataset.folder) : null
        if (evt.from === evt.to && evt.oldIndex === evt.newIndex) return
        const { ok, data } = await api('POST', `/api/notes/${noteId}/move`, {
          folderId,
          position: evt.newIndex,
        })
        if (!ok) {
          toast(data?.error || tr('Er ging iets mis.'))
          window.location.reload()
        }
      },
    })
  })
  document.querySelectorAll('[data-folder-list]').forEach((el) => {
    Sortable.create(el, {
      ...common,
      group: 'folders',
      draggable: '.tree-folder',
      async onEnd(evt) {
        const folderId = evt.item.dataset.folderId
        const parentId = evt.to.dataset.parent ? Number(evt.to.dataset.parent) : null
        if (evt.from === evt.to && evt.oldIndex === evt.newIndex) return
        const { ok, data } = await api('POST', `/api/folders/${folderId}/move`, {
          parentId,
          position: evt.newIndex,
        })
        if (!ok) toast(data?.error || tr('Er ging iets mis.'))
        if (!ok || evt.from !== evt.to) window.location.reload()
      },
    })
  })
}

window.Alpine = Alpine
document.addEventListener('DOMContentLoaded', () => Alpine.start())

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}))
}
