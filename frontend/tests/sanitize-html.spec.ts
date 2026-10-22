import assert from 'node:assert/strict'
import test from 'node:test'

import { safeInlineMarkdown, safeRichHtml } from '../src/lib/sanitizeHtml.ts'

test('safeInlineMarkdown escapes HTML before applying inline formatting', () => {
  const html = safeInlineMarkdown('<img src=x onerror=alert(1)>**ok**')

  assert.equal(html.includes('<img'), false)
  assert.equal(html.includes('onerror'), false)
  assert.equal(html.includes('&lt;img'), true)
  assert.equal(html.includes('<strong>ok</strong>'), true)
})

test('safeRichHtml removes script tags, event handlers, and javascript URLs', () => {
  const html = safeRichHtml(`
    <p onclick="alert(1)">正文</p>
    <script>alert(1)</script>
    <img src="javascript:alert(1)" onerror="alert(2)" alt="x">
  `)

  assert.equal(/<script/i.test(html), false)
  assert.equal(/onclick/i.test(html), false)
  assert.equal(/onerror/i.test(html), false)
  assert.equal(/javascript:/i.test(html), false)
  assert.equal(html.includes('正文'), true)
})
