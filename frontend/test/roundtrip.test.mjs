import assert from 'node:assert/strict'
import { test } from 'node:test'

import { JSDOM } from 'jsdom'

const dom = new JSDOM('<!doctype html><div id="e"></div>')
const globals = {
  window: dom.window,
  document: dom.window.document,
  navigator: dom.window.navigator,
  HTMLElement: dom.window.HTMLElement,
  Node: dom.window.Node,
  getComputedStyle: dom.window.getComputedStyle,
}
for (const [key, value] of Object.entries(globals)) {
  // Newer Node versions define some of these (navigator) as read-only getters.
  Object.defineProperty(globalThis, key, { value, configurable: true, writable: true })
}
const { createEditor, getMarkdown } = await import('../src/editor/setup.js')

function roundtrip(src) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const editor = createEditor({ element: el, markdown: src })
  const out = getMarkdown(editor)
  editor.destroy()
  return out
}

const CASES = {
  report: `**Datum:** 2026-10-14 · **Deelnemers:** Cas, Jan · **Context:** [[Klant X]]

## Actiepunten

- [ ] Offerte sturen 📅 2026-10-20 14:00 🆔 k3f9x
- [x] Agenda delen ✅ 2026-10-14 🆔 p2m7q
  - [ ] Subtaak 🆔 abcde
`,
  prose: `foo_bar en 3 * 4 = 12, #tag, a < b > c & d, 100% (zeker), C:\\pad.

Zie [[Projectplan fase 2|plan]] en [link](https://example.com) en \`code\` en https://example.com/a_b
`,
  lists: `1. een
2. twee

- a
- b
`,
  blocks: `> quote

\`\`\`python
print("hi_there *x*")
\`\`\`

![schets](/attachments/01JA8K0000000000000000000/schets.png)
`,
  details: `<details>
<summary>Ruwe aantekeningen</summary>

jan - budget ok

offerte voor 20e!!

</details>
`,
  table: `| a   | b   |
| --- | --- |
| 1   | 2   |
`,
  escapes: `\\*niet vet\\* en \\_ook niet\\_ en \\[geen](link)
`,
}

for (const [name, src] of Object.entries(CASES)) {
  test(`roundtrip: ${name}`, () => {
    assert.equal(roundtrip(src), src)
  })
}

test('image width is kept in the alt text', () => {
  const src = '![schets|320](/attachments/01JA8K0000000000000000000/schets.png)\n'
  assert.equal(roundtrip(src), src)
  const el = document.createElement('div')
  const editor = createEditor({ element: el, markdown: src })
  let width = null
  editor.state.doc.descendants((n) => {
    if (n.type.name === 'image') width = n.attrs.width
  })
  editor.destroy()
  assert.equal(width, 320)
})
