import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const frontendSrc = join(dirname(fileURLToPath(import.meta.url)), '../src')

function readSrc(rel: string): string {
  return readFileSync(join(frontendSrc, rel), 'utf8')
}

test('进度页不把流程说明书做成彩色信息盒', () => {
  const extract = readSrc('pages/RequirementReview/components/ExtractionProgressPanel.tsx')
  const generic = readSrc('components/GenericProgressPage.tsx')
  const reviewProgress = readSrc('pages/Progress.tsx')
  const collusionProgress = readSrc('pages/CollusionProgress.tsx')
  const header = readSrc('pages/RequirementReview/components/RequirementHeader.tsx')
  const confirmPage = readSrc('pages/RequirementReview/index.tsx')

  assert.match(extract, /核验、合并与评分提取通常较久，完成后进入招标要求确认/)
  assert.doesNotMatch(extract, /bg-blue-50 rounded-lg border border-blue-200/)
  assert.doesNotMatch(extract, /<strong>说明:/)

  assert.match(generic, /text-gray-500 mt-1/)
  assert.match(generic, /config\.description/)
  assert.doesNotMatch(generic, /descriptionColor/)
  assert.doesNotMatch(generic, /<strong>说明:/)

  assert.match(reviewProgress, /完成后进入审查结果/)
  assert.doesNotMatch(reviewProgress, /无需人工干预/)
  assert.doesNotMatch(reviewProgress, /descriptionColor/)

  assert.match(collusionProgress, /深度分析通常需要 5–15 分钟，完成后进入分析结果/)
  assert.doesNotMatch(collusionProgress, /全程自动无需干预/)
  assert.doesNotMatch(collusionProgress, /descriptionColor/)

  assert.match(header, /确认后开始响应提取与对比审查。否决项不符合即不通过/)
  assert.doesNotMatch(confirmPage, /招标要求是AI审查的基准/)
  assert.doesNotMatch(confirmPage, /bg-yellow-50 rounded-lg border border-yellow-200/)
})
