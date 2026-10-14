// Keep the Markdown we write close to what a person (or an AI) would type.
//
// @tiptap/markdown escapes every `*`, `_`, `[`, `]`, `~` and HTML character in text.
// That is safe but turns "foo_bar" into "foo\_bar" and "[[Link]]" into "\[\[Link\]\]".
// This escaper only escapes where the character could actually start markup.

const PUNCT = /[!-/:-@[-`{-~]/

function escapeText(text, atLineStart) {
  let out = ''
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    const prev = text[i - 1] ?? ''
    const next = text[i + 1] ?? ''
    const rest = text.slice(i + 1)
    switch (ch) {
      case '\\':
        out += PUNCT.test(next) ? '\\\\' : '\\'
        break
      case '`':
        out += '\\`'
        break
      case '*':
        out += /\s/.test(prev || ' ') && /\s/.test(next || ' ') ? '*' : '\\*'
        break
      case '_': {
        const intraword = /[\p{L}\p{N}]/u.test(prev) && /[\p{L}\p{N}]/u.test(next)
        out += intraword ? '_' : '\\_'
        break
      }
      case '~':
        out += next === '~' || prev === '~' ? '\\~' : '~'
        break
      case '[': {
        // Wikilinks stay as typed; only escape what could become a link.
        if (next === '[' && /^\[[^[\]\n]+\]\]/.test(rest)) {
          const end = rest.indexOf(']]') + 2
          out += '[' + rest.slice(0, end)
          i += end
          break
        }
        out += /^[^\]]*\](\(|\[|:)/.test(rest) ? '\\[' : '['
        break
      }
      case '<':
        out += /^[A-Za-z/!?]/.test(next) ? '&lt;' : '<'
        break
      case '&':
        out += /^#?[A-Za-z0-9]+;/.test(rest) ? '&amp;' : '&'
        break
      default:
        out += ch
    }
  }
  if (atLineStart) {
    out = out.replace(/^(#{1,6}\s|>|[-+]\s|\d+)([.)]\s)?/, (m, a, b) => {
      if (/^\d+$/.test(a) && b) return a + '\\' + b
      if (/^\d+$/.test(a)) return m
      return '\\' + m
    })
  }
  return out
}

export function patchMarkdown(editor) {
  const manager = editor.markdown
  const codeTypes = manager.codeTypes
  manager.encodeTextForMarkdown = function (text, node, parentNode) {
    const inCode =
      (parentNode?.type != null && codeTypes.has(parentNode.type)) ||
      (node.marks || []).some((m) => codeTypes.has(typeof m === 'string' ? m : m.type))
    if (inCode) return text
    const first = Array.isArray(parentNode?.content) && parentNode.content[0] === node
    return escapeText(text, first && parentNode?.type === 'paragraph')
  }
}

export { escapeText }
