import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { buildExportFilename } from '../src/lib/download.ts'

test('三大流程导出文件名按项目名+流程类型拼接', () => {
  assert.equal(buildExportFilename('市政道路', '投标审查', 'pdf'), '市政道路投标审查.pdf')
  assert.equal(buildExportFilename('市政道路', '串标审查', 'pdf'), '市政道路串标审查.pdf')
  assert.equal(buildExportFilename('市政道路', '标书初稿', 'docx'), '市政道路标书初稿.docx')
})

test('空项目名回退为未命名项目', () => {
  assert.equal(buildExportFilename('', '投标审查', 'pdf'), '未命名项目投标审查.pdf')
  assert.equal(buildExportFilename('  ', '标书初稿', 'docx'), '未命名项目标书初稿.docx')
  assert.equal(buildExportFilename(null, '串标审查', 'pdf'), '未命名项目串标审查.pdf')
  assert.equal(buildExportFilename(undefined, '投标审查', 'pdf'), '未命名项目投标审查.pdf')
})

test('项目名中的非法路径字符被替换', () => {
  const name = buildExportFilename('公路<a>/b:c', '投标审查', 'pdf')
  assert.equal(name.includes('/'), false)
  assert.equal(name.includes('\\'), false)
  assert.equal(name.includes(':'), false)
  assert.equal(name.includes('<'), false)
  assert.match(name, /投标审查\.pdf$/)
})

test('下载页必须使用统一导出命名，禁止继续用项目ID或英文前缀', () => {
  const resultSource = readFileSync(resolve('src/pages/Result.tsx'), 'utf8')
  const collusionSource = readFileSync(resolve('src/pages/CollusionResult.tsx'), 'utf8')
  const writingSource = readFileSync(resolve('src/pages/writing/WritingPreview.tsx'), 'utf8')

  assert.match(resultSource, /buildExportFilename\(projectName, '投标审查', 'pdf'\)/)
  assert.match(collusionSource, /buildExportFilename\(projectName, '串标审查', 'pdf'\)/)
  assert.match(writingSource, /buildExportFilename\(projectName, '标书初稿', 'docx'\)/)

  assert.doesNotMatch(resultSource, /审查报告_\$\{projectId\}/)
  assert.doesNotMatch(collusionSource, /串标审查报告_\$\{projectId\}/)
  assert.doesNotMatch(writingSource, /bid_document_\$\{pid\}/)
})
