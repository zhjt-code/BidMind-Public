import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import { isChunkLoadError } from '../src/lib/chunkLoadError.ts'

const frontendSrc = join(dirname(fileURLToPath(import.meta.url)), '../src')

test('Chrome 动态 import 失败视为 chunk load error', () => {
  const error = new Error(
    'Failed to fetch dynamically imported module: http://localhost:3000/src/pages/result/documentQuality/DocumentQualityView.tsx',
  )
  assert.equal(isChunkLoadError(error), true)
})

test('同形态第二例：Firefox 模块脚本失败同样识别，普通渲染错误不误伤', () => {
  assert.equal(
    isChunkLoadError(new Error('error loading dynamically imported module')),
    true,
  )
  assert.equal(isChunkLoadError(new Error('Cannot read properties of null')), false)
})

test('ErrorBoundary 对 chunk load 走整页刷新，而不是只清 state', () => {
  const source = readFileSync(join(frontendSrc, 'components/ErrorBoundary.tsx'), 'utf8')
  assert.match(source, /isChunkLoadError/)
  assert.match(source, /window\.location\.reload/)
})

test('预览 hook 固定 .ts 且不含 JSX，避免无后缀 import 仍去拉已删除的旧扩展名', () => {
  const hookTs = join(frontendSrc, 'pages/result/pdfPreview/usePdfEvidencePreview.ts')
  const hookTsx = join(frontendSrc, 'pages/result/pdfPreview/usePdfEvidencePreview.tsx')
  assert.equal(existsSync(hookTs), true)
  assert.equal(existsSync(hookTsx), false)
  const source = readFileSync(hookTs, 'utf8')
  assert.doesNotMatch(source, /<PdfPreviewDialog/)
  assert.doesNotMatch(source, /<PdfEvidencePreviewHost/)
})
