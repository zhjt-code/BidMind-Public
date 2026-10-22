import assert from 'node:assert/strict'
import test from 'node:test'

import {
  hasRichClauseMarkup,
  shortUserFacingClauseText,
  shouldApplyClauseHeadingNumbers,
  toUserFacingClauseText,
} from '../src/pages/writing/writingClauseDisplay.ts'
import { getBindingLabel } from '../src/pages/writing/WritingBinding/bindingLabel.ts'
import type { WritingBinding } from '../src/services/writingApi.ts'

const outlineJson = JSON.stringify({
  has_explicit_directory: true,
  volumes: [
    {
      volume_type: 'technical',
      volume_title: '技术标',
      items: [
        {
          order: 1,
          title: '投标函',
          node_type: 'fixed_template',
          node_type_source: 'llm_outline',
        },
        {
          order: 2,
          title: '施工组织设计',
          node_type: 'free_writing',
          node_type_source: 'llm_outline',
        },
      ],
    },
  ],
})

test('toUserFacingClauseText 将目录 JSON 约束转换为中文摘要', () => {
  const result = toUserFacingClauseText(outlineJson)

  assert.match(result, /目录结构：招标文件已明确目录/)
  assert.match(result, /技术标：投标函（固定模板）、施工组织设计（自由论述）/)
  assert.doesNotMatch(result, /has_explicit_directory|node_type|node_type_source|fixed_template|free_writing/)
  assert.doesNotMatch(result, /[{}"]/)
})

test('toUserFacingClauseText 将结构模式 JSON 约束转换为中文摘要', () => {
  const result = toUserFacingClauseText(JSON.stringify({
    structure_mode: 'tech_comm',
    has_technical: true,
    has_commercial: true,
    has_qualification: false,
    technical_sections: [],
    commercial_sections: ['报价函', '投标报价表'],
    qualification_sections: [],
    fixed_template_sections: ['法定代表人身份证明'],
  }))

  assert.match(result, /结构模式：技术标 \+ 商务标/)
  assert.match(result, /商务标：报价函、投标报价表/)
  assert.match(result, /固定模板：法定代表人身份证明/)
  assert.doesNotMatch(result, /structure_mode|has_technical|fixed_template_sections/)
})

test('shortUserFacingClauseText 截断前先清洗内部 JSON 字段', () => {
  const result = shortUserFacingClauseText(outlineJson, 32)

  assert.equal(result.endsWith('...'), true)
  assert.doesNotMatch(result, /has_explicit_directory|node_type|fixed_template/)
  assert.match(result, /^目录结构：/)
})

test('普通中文约束保持原文展示', () => {
  const plain = '投标文件应按招标文件要求加盖公章并签字。'

  assert.equal(toUserFacingClauseText(plain), plain)
  assert.equal(shortUserFacingClauseText(plain, 100), plain)
})

test('多行 Markdown 行业画像保留换行与行首标题', () => {
  const markdown = [
    '### 1. 行业识别与典型特征',
    '- **所属行业**：石油天然气技术服务行业。',
    '- **常见结构**：商务标、技术标。',
    '',
    '### 2. 专业术语表（20-30个）',
    '1.  多相流',
    '2.  腐蚀速率',
    '',
    '### 3. 技术方案典型结构',
    '- **第一章 项目概述**',
  ].join('\n')

  const result = toUserFacingClauseText(markdown)

  assert.ok(result.includes('\n'), '全文展示不得折叠换行')
  assert.match(result, /^### 1\. 行业识别与典型特征/m)
  assert.match(result, /^### 2\. 专业术语表（20-30个）/m)
  assert.match(result, /^### 3\. 技术方案典型结构/m)
  assert.doesNotMatch(result, /技术标。 ### 2\./)
})

test('内部 token 清洗不吞换行，摘要路径仍可压成单行', () => {
  const mixed = [
    '技术方案要求如下：',
    'node_type=free_writing; structure_mode=tech_comm',
    '### 后续章节',
  ].join('\n')

  const full = toUserFacingClauseText(mixed)
  assert.ok(full.includes('\n'))
  assert.match(full, /^### 后续章节/m)
  assert.doesNotMatch(full, /node_type|structure_mode|free_writing|tech_comm/)

  const short = shortUserFacingClauseText(mixed, 80)
  assert.equal(short.includes('\n'), false)
})

test('六段契约标题才套中文序号，手写一、或小数点标题不套', () => {
  const contract = [
    '### 1. 行业识别与典型特征',
    '- 物业服务',
    '### 2. 专业术语表（20-30个）',
    '- 秩序维护',
  ].join('\n')
  const essay = [
    '### 一、项目背景和政策依据',
    '• 政策加码',
    '### 3.1 核心产品竞争力分析',
  ].join('\n')
  const certEssay = [
    '### 一、资质与证书隔离要求',
    '• 不得出现证书',
    '### 3.1 有效期与发证机关',
  ].join('\n')

  assert.equal(shouldApplyClauseHeadingNumbers(contract), true)
  assert.equal(shouldApplyClauseHeadingNumbers(essay), false)
  assert.equal(shouldApplyClauseHeadingNumbers(certEssay), false)
  assert.equal(hasRichClauseMarkup(essay), true)
})

test('getBindingLabel 约束类标签不暴露 JSON 技术参数', () => {
  const binding = {
    id: 1,
    projectId: 1,
    outlineNodeId: 10,
    clauseId: 100,
    materialId: null,
    bindingType: 'clause_to_node',
    confidence: 0.8,
    source: 'auto',
    status: 'suggested',
    reason: null,
    scope: null,
  } as WritingBinding

  const label = getBindingLabel(
    binding,
    new Map([[100, { content: outlineJson, subType: 'bid_directory_spec' }]]),
    new Map(),
  )

  assert.equal(label.tag, '约束')
  assert.match(label.text, /^目录结构：/)
  assert.match(label.fullText, /技术标：投标函/)
  assert.doesNotMatch(label.text + label.fullText, /node_type|fixed_template|has_explicit_directory/)
})
