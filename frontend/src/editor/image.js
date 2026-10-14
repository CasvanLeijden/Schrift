import Image from '@tiptap/extension-image'

// Images can be resized by dragging a corner. The width is stored the way Obsidian does
// it, in the alt text: ![schets|320](/attachments/...). Other renderers just show the alt.
const ALT_WIDTH_RE = /^(.*?)\|(\d{2,4})(?:x\d{2,4})?$/s

export const SchriftImage = Image.extend({
  parseMarkdown(token, h) {
    const m = (token.text || '').match(ALT_WIDTH_RE)
    return h.createNode('image', {
      src: token.href,
      title: token.title,
      alt: m ? m[1] : token.text,
      width: m ? Number(m[2]) : null,
    })
  },
  renderMarkdown(node) {
    const { src = '', alt = '', title = '', width } = node.attrs || {}
    const label = (alt || '') + (width ? `|${Math.round(width)}` : '')
    return title ? `![${label}](${src} "${title}")` : `![${label}](${src})`
  },
}).configure({
  resize: {
    enabled: true,
    directions: ['bottom-right', 'bottom-left'],
    minWidth: 60,
    minHeight: 40,
    alwaysPreserveAspectRatio: true,
  },
})
