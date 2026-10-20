/**
 * 职责: 锁定写标预览/重新渲染请求不继承 httpClient 30s 默认超时
 * 不做: 不测 iframe 渲染、不测后端快照生成、不改下载 Word 超时
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

function extractFunctionBlock(source: string, fnName: string, nextFnName: string): string {
  const start = source.indexOf(`${fnName}:`)
  const end = source.indexOf(`${nextFnName}:`, start + 1)
  assert.ok(start >= 0, `未找到 ${fnName}`)
  assert.ok(end > start, `未找到 ${fnName} 的结束锚点 ${nextFnName}`)
  return source.slice(start, end)
}

test('httpClient 默认超时仍为 30s，避免全局拉长所有接口', () => {
  const httpClientSource = readSource('../src/services/httpClient.ts')
  assert.match(httpClientSource, /timeout:\s*30000/)
})

test('初次 HTML 预览超时为 3 分钟，覆盖长文档首次渲染', () => {
  const source = readSource('../src/services/writingApi.ts')
  const block = extractFunctionBlock(source, 'getPreviewHtml', 'getPreviewUrl')
  assert.match(block, /timeout:\s*180000/)
  assert.doesNotMatch(block, /timeout:\s*30000/)
})

test('同页重新渲染超时与预览对齐为 3 分钟', () => {
  const source = readSource('../src/services/writingApi.ts')
  const block = extractFunctionBlock(source, 'triggerRender', 'getPreviewHtml')
  assert.match(block, /timeout:\s*180000/)
  assert.doesNotMatch(block, /timeout:\s*30000/)
})
