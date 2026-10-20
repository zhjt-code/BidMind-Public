import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const frontendSrc = join(dirname(fileURLToPath(import.meta.url)), '../src')

function readSrc(rel: string): string {
  return readFileSync(join(frontendSrc, rel), 'utf8')
}

test('TruncatedHoverText 用 Tooltip 展示全称，自身不加 truncate', () => {
  const src = readSrc('components/upload/TruncatedHoverText.tsx')
  assert.match(src, /TooltipTrigger/)
  assert.match(src, /TooltipContent/)
  assert.match(src, /\{text\}/)
  assert.doesNotMatch(src, /truncate/)
})

test('投标审查上传槽：已选文件与已有文档文件名都走悬停全称', () => {
  const src = readSrc('pages/fileUploadParts/UploadSlotArea.tsx')
  assert.match(src, /TruncatedHoverText text=\{file\.name\}/)
  assert.match(src, /TruncatedHoverText text=\{existingVisible\.filename\}/)
  assert.match(src, /truncate max-w-xs/)
})

test('串标上传：招标与投标文件名（新选/已有）都走同一悬停全称', () => {
  const src = readSrc('pages/CollusionUpload.tsx')
  assert.match(src, /TruncatedHoverText text=\{existingTender\.filename\}/)
  assert.match(src, /TruncatedHoverText text=\{tenderFile\.name\}/)
  assert.match(src, /TruncatedHoverText text=\{doc\.filename\}/)
  assert.match(src, /TruncatedHoverText text=\{f\.name\}/)
})

test('同形态第二例：门禁弹窗与编写素材上传的截断文件名也走悬停全称', () => {
  const gate = readSrc('components/DocGateWarningDialog.tsx')
  const material = readSrc('pages/writing/MaterialUploadDialog.tsx')
  const dup = readSrc('pages/collusionUploadParts/DuplicateWarningDialog.tsx')
  assert.match(gate, /TruncatedHoverText text=\{item\.filename\}/)
  assert.match(material, /TruncatedHoverText text=\{item\.file\.name\}/)
  assert.match(dup, /TruncatedHoverText text=\{name\}/)
})
