import { Editor } from '@tiptap/core'
import { DetailsContent, DetailsSummary } from '@tiptap/extension-details'
import Link from '@tiptap/extension-link'
import { TaskItem, TaskList } from '@tiptap/extension-list'
import { TableKit } from '@tiptap/extension-table'
import { Placeholder } from '@tiptap/extensions'
import { Markdown } from '@tiptap/markdown'
import StarterKit from '@tiptap/starter-kit'

import { SchriftDetails } from './details.js'
import { SchriftImage } from './image.js'
import { patchMarkdown } from './mdfix.js'
import { Tokens } from './tokens.js'

export function createEditor({
  element,
  markdown,
  placeholder = '',
  lang = 'nl',
  extensions = [],
  tokens = {},
}) {
  const editor = new Editor({
    element,
    extensions: [
      StarterKit.configure({ link: false }),
      Link.configure({ openOnClick: false, autolink: true, linkOnPaste: true }),
      Markdown.configure({ indentation: { style: 'space', size: 2 } }),
      TaskList,
      TaskItem.configure({ nested: true }),
      SchriftImage,
      TableKit.configure({ table: { resizable: false } }),
      SchriftDetails.configure({ persist: true, HTMLAttributes: { class: 'details' } }),
      DetailsSummary,
      DetailsContent,
      Placeholder.configure({ placeholder }),
      Tokens.configure({ lang, ...tokens }),
      ...extensions,
    ],
    content: markdown,
    contentType: 'markdown',
  })
  patchMarkdown(editor)
  return editor
}

export function getMarkdown(editor) {
  const out = editor
    .getMarkdown()
    // Bare URLs stay bare instead of becoming [url](url).
    .replace(/\[((?:https?:\/\/|mailto:)[^\]\s]+)\]\(\1\)/g, '$1')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  return out ? out + '\n' : ''
}
