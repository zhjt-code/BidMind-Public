import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { fileURLToPath, URL } from 'node:url'
import test from 'node:test'
import postcss from 'postcss'
import tailwindcss from '@tailwindcss/postcss'

const root = fileURLToPath(new URL('../', import.meta.url))

test('compiled utilities preserve scoped domain colors, compact shadows and fixed panes', async () => {
  const from = fileURLToPath(new URL('../src/index.css', import.meta.url))
  const source = await readFile(from, 'utf8')
  const result = await postcss([tailwindcss({ base: root, optimize: false })]).process(
    source + '\n@source inline("bg-primary shadow-xs shrink-0 animate-accordion-down");',
    { from },
  )
  const rules = new Map()
  result.root.walkRules(rule => {
    if (['.bg-primary', '.shadow-xs', '.shrink-0'].includes(rule.selector)) {
      rules.set(rule.selector, rule.toString())
    }
  })
  assert.match(rules.get('.bg-primary') ?? '', /background-color:\s*hsl\(var\(--primary\)\)/)
  assert.match(rules.get('.shadow-xs') ?? '', /0\s+1px\s+2px/)
  assert.match(rules.get('.shrink-0') ?? '', /flex-shrink:\s*0/)
  assert.match(result.css, /@keyframes accordion-down/)
  assert.match(result.css, /button:not\(:disabled\)[\s\S]*?cursor:\s*pointer/)
  assert.match(result.css, /\.ProseMirror table/)
})
