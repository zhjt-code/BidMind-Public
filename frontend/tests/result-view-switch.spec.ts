import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import {
  segmentedCountAnnouncement,
  segmentedOptionAriaLabel,
} from '../src/components/upload/segmentedOptionAria.ts'

const frontendSrc = join(dirname(fileURLToPath(import.meta.url)), '../src')

function readSrc(rel: string): string {
  return readFileSync(join(frontendSrc, rel), 'utf8')
}

test('分段计数朗读后缀按单位拼接，不绑死质检实体', () => {
  assert.equal(segmentedCountAnnouncement(35, '项待处理'), '35 项待处理')
  assert.equal(segmentedCountAnnouncement(3, '条未读'), '3 条未读')
  assert.equal(segmentedCountAnnouncement(0, '项待处理'), undefined)
  assert.equal(segmentedCountAnnouncement(-1, '项待处理'), undefined)
  assert.equal(segmentedOptionAriaLabel('文档质检', '35 项待处理'), '文档质检，35 项待处理')
  assert.equal(segmentedOptionAriaLabel('账务流水', '3 条未读'), '账务流水，3 条未读')
  assert.equal(segmentedOptionAriaLabel('响应审查'), '响应审查')
  assert.equal(segmentedOptionAriaLabel('文档质检', '  '), '文档质检')
})

test('分段器以通用 accessory/ariaLabel 承载附件，不写业务实体词', () => {
  const segmented = readSrc('components/upload/SegmentedControl.tsx')
  assert.match(segmented, /accessory\?:/)
  assert.match(segmented, /ariaLabel\?:/)
  assert.match(segmented, /option\.accessory/)
  assert.match(segmented, /option\.ariaLabel/)
  assert.match(segmented, /inline-flex items-center/)
  assert.doesNotMatch(segmented, /文档质检/)
  assert.doesNotMatch(segmented, /qualityPending/)
})

test('结果页工作区切换复用项目级分段器，不再用 shadcn Tabs', () => {
  const resultPage = readSrc('pages/Result.tsx')
  const viewSwitch = readSrc('pages/result/ResultViewSwitch.tsx')
  assert.match(resultPage, /<ResultViewSwitch/)
  assert.doesNotMatch(resultPage, /from '@\/components\/ui\/tabs'/)
  assert.doesNotMatch(resultPage, /TabsTrigger/)
  assert.match(viewSwitch, /SegmentedControl/)
  assert.match(viewSwitch, /emphasized/)
  assert.match(viewSwitch, /ariaLabel=\{RESULT_VIEW_LABEL\}/)
  assert.match(viewSwitch, /const RESULT_VIEW_LABEL = '结果视图'/)
  assert.match(viewSwitch, /FieldHintButton/)
  assert.match(viewSwitch, /segmentedCountAnnouncement/)
  assert.match(viewSwitch, /pendingCount/)
  assert.doesNotMatch(viewSwitch, /bg-amber-500/)
  assert.doesNotMatch(viewSwitch, /accessory/)
  assert.doesNotMatch(viewSwitch, /resolveDomainAccent/)
  assert.doesNotMatch(viewSwitch, /TabsList/)
  assert.doesNotMatch(viewSwitch, /bg-primary/)
})

test('页级工作区切换内联在标题后，不做成表单标签行', () => {
  const resultPage = readSrc('pages/Result.tsx')
  const viewSwitch = readSrc('pages/result/ResultViewSwitch.tsx')
  const knowledgePanel = readSrc('components/KnowledgeUsagePanel.tsx')

  const titleThenSwitch = resultPage.match(
    /<h1 className="text-xl font-bold text-gray-900 shrink-0">投标审查结果<\/h1>\s*<div className="mx-1 h-5 w-px shrink-0 bg-gray-200" aria-hidden \/>\s*<ResultViewSwitch/,
  )
  assert.ok(titleThenSwitch, 'ResultViewSwitch 必须经发丝竖线紧跟页标题')
  assert.doesNotMatch(resultPage, /<\/div>\s*<ResultViewSwitch/)
  assert.doesNotMatch(resultPage, /h-5 w-px bg-gray-200 mx-4/)
  assert.doesNotMatch(viewSwitch, /<p className="shrink-0 text-sm font-medium/)
  assert.doesNotMatch(viewSwitch, /px-1 pb-2 flex-shrink-0/)
  assert.doesNotMatch(viewSwitch, /export const RESULT_VIEW_LABEL/)

  // 同形态反例: 配置区字段开关仍保留可见标签，不随页头内联
  assert.match(knowledgePanel, /KNOWLEDGE_USAGE_LABEL/)
  assert.match(knowledgePanel, /<p className="shrink-0 text-sm font-medium text-\[#1c2733\]">\{KNOWLEDGE_USAGE_LABEL\}<\/p>/)

  // 顶栏分区线保持宽边距；页头复合标题用工具条尺度 mx-1
  const header = readSrc('components/Header.tsx')
  const pdfChrome = readSrc('pages/result/pdfPreview/PdfPreviewChrome.tsx')
  assert.match(header, /h-5 w-px bg-gray-200 mx-4/)
  assert.match(pdfChrome, /mx-1 h-5 w-px bg-gray-200/)
})
