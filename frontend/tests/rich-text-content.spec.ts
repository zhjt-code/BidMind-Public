import assert from 'node:assert/strict'
import test from 'node:test'

import { stripMarkdownSyntax } from '../src/components/RichTextContent.tsx'

// ── stripMarkdownSyntax ─────────────────────────────────────────────────────

test('stripMarkdownSyntax: 去除标题前缀', () => {
  assert.equal(stripMarkdownSyntax('## 风险分析'), '风险分析')
  assert.equal(stripMarkdownSyntax('### 详细说明'), '详细说明')
  assert.equal(stripMarkdownSyntax('# 一级标题'), '一级标题')
})

test('stripMarkdownSyntax: 去除无序列表前缀', () => {
  assert.equal(stripMarkdownSyntax('- 第一条'), '第一条')
  assert.equal(stripMarkdownSyntax('* 第二条'), '第二条')
})

test('stripMarkdownSyntax: 去除有序列表前缀', () => {
  assert.equal(stripMarkdownSyntax('1. 第一条'), '第一条')
  assert.equal(stripMarkdownSyntax('12. 第十二条'), '第十二条')
})

test('stripMarkdownSyntax: 去除粗体和斜体标记', () => {
  assert.equal(stripMarkdownSyntax('这是**重要**内容'), '这是重要内容')
  assert.equal(stripMarkdownSyntax('这是*斜体*内容'), '这是斜体内容')
})

test('stripMarkdownSyntax: 多行混合格式', () => {
  const input = '## 标题\n- 列表项\n普通文本\n**粗体**'
  const expected = '标题\n列表项\n普通文本\n粗体'
  assert.equal(stripMarkdownSyntax(input), expected)
})

test('stripMarkdownSyntax: 纯文本不变', () => {
  const plain = '这是一段普通文本，没有任何Markdown语法。'
  assert.equal(stripMarkdownSyntax(plain), plain)
})

test('stripMarkdownSyntax: 空值安全', () => {
  assert.equal(stripMarkdownSyntax(null), '')
  assert.equal(stripMarkdownSyntax(undefined), '')
  assert.equal(stripMarkdownSyntax(''), '')
})

test('stripMarkdownSyntax: 保留正常的连字符和星号使用', () => {
  assert.equal(stripMarkdownSyntax('温度范围: -10~40'), '温度范围: -10~40')
  assert.equal(stripMarkdownSyntax('评分: 4.5*满分5'), '评分: 4.5*满分5')
})
