import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { buildFindingListSummary } from '../src/pages/collusionResult/evidenceSummary.ts'

const collusionResultSource = readFileSync(resolve('src/pages/CollusionResult.tsx'), 'utf8')

test('串标证据中栏列表应使用概要文本而非完整详情', () => {
  assert.match(collusionResultSource, /buildFindingListSummary/)
  assert.doesNotMatch(
    collusionResultSource,
    /\{stripMarkdownSyntax\(replaceRefs\(finding\.description\)\)\}/,
    '中栏列表不得直接渲染完整 finding.description，详情应交给右栏 PairDetail 展示',
  )
})

test('D8 文档元数据概要应去除重复工具详情并保留降权原因', () => {
  const description = 'D8 文档元数据：PDF生成工具相同：Microsoft® Word 适用于 Microsoft 365（命中通用 PDF 工具白名单，已折扣为弱信号）；PDF生成器相同：Microsoft® Word 适用于 Microsoft 365（命中通用 PDF 工具白名单，已折扣为弱信号）'

  const summary = buildFindingListSummary({ dimension: 'D8', description })

  assert.equal(
    summary,
    'PDF生成工具相同、PDF生成器相同（命中通用 PDF 工具白名单，已折扣为弱信号）',
  )
  assert.ok(!summary.includes('Microsoft® Word'), '列表概要不应重复展示具体工具长名称')
})

test('普通长证据描述应裁剪为扫读摘要', () => {
  const summary = buildFindingListSummary({
    dimension: 'D3',
    description: '**技术方案文本雷同**：双方核心段落连续多处一致，并且出现相同非标准表述，需要进一步核查来源。',
  }, { maxLength: 28 })

  assert.ok(summary.length <= 28)
  assert.ok(summary.endsWith('...'))
  assert.ok(!summary.includes('**'))
})
