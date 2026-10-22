import assert from 'node:assert/strict'
import test from 'node:test'

import { safeInlineMarkdown } from '../src/lib/sanitizeHtml.ts'

test('WritingClauses markdown path escapes hostile clause content', () => {
  const clauseContent = '## 标题\n- <img src=x onerror=alert(1)> **保留加粗**'
  const renderedListItem = safeInlineMarkdown(
    clauseContent.split('\n')[1].replace(/^[*-]\s+/, ''),
  )

  assert.equal(renderedListItem.includes('<img'), false)
  assert.equal(renderedListItem.includes('onerror'), false)
  assert.equal(renderedListItem.includes('&lt;img'), true)
  assert.equal(renderedListItem.includes('<strong>保留加粗</strong>'), true)
})
