import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { copyPdfjsAssetsTo } from '../vite-plugin-pdfjs-assets.ts'

const frontendRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const frontendSrc = join(frontendRoot, 'src')

function readSrc(rel: string): string {
  return readFileSync(join(frontendSrc, rel), 'utf8')
}

function readFrontend(rel: string): string {
  return readFileSync(join(frontendRoot, rel), 'utf8')
}

test('PdfPreviewDialog 用 pdf.js 画布栅格化，禁止原生 PDF 插件 iframe', () => {
  const source = readSrc('pages/result/PdfPreviewDialog.tsx')

  assert.match(source, /from 'react-pdf'/)
  assert.match(source, /<Document/)
  assert.match(source, /<Page/)
  assert.match(source, /renderMode="canvas"/)
  assert.match(source, /PDFJS_WORKER_SRC/)
  assert.match(source, /GlobalWorkerOptions\.workerSrc/)
  assert.match(source, /PDFJS_DOCUMENT_OPTIONS/)
  assert.match(source, /formatPdfParseError/)
  assert.doesNotMatch(source, /<iframe/)
  assert.doesNotMatch(source, /createObjectURL/)
  assert.doesNotMatch(source, /<embed/)
  assert.doesNotMatch(source, /<object/)
  assert.doesNotMatch(source, /pdf\.worker\.min\.mjs\?url/)
})

test('PdfPreviewDialog 接受 excerpts，按当前页文本区定位，禁止失败时整页假框', () => {
  const source = readSrc('pages/result/PdfPreviewDialog.tsx')
  assert.match(source, /excerpts\?:/)
  assert.match(source, /locateHighlights/)
  assert.match(source, /textItemsToRegions/)
  assert.match(source, /selectPdfjsTextItems/)
  assert.doesNotMatch(source, /Boolean\(item\)/)
  assert.doesNotMatch(source, /PAGE_BBOX/)
})

test('同形态：审查/要求确认/写标/串标/质检把摘录或几何框交给同一预览器', () => {
  const hosts: Array<[string, RegExp]> = [
    ['pages/result/ResultDetail.tsx', /excerpts=\{pdfExcerpts\}/],
    ['pages/RequirementReview/index.tsx', /excerpts=\{pdfExcerpts\}/],
    ['pages/writing/WritingClauses.tsx', /usePdfEvidencePreview/],
    ['pages/writing/WritingMaterials/index.tsx', /excerpts=\{preview\.excerpts\}/],
    ['pages/collusionResult/PairDetail.tsx', /excerpts: trimmed \? \[trimmed\] : \[\]/],
    ['pages/result/documentQuality/DocumentQualityDetail.tsx', /usePdfEvidencePreview/],
  ]
  for (const [rel, pattern] of hosts) {
    const source = readSrc(rel)
    assert.match(source, pattern, `${rel} 必须把摘录或几何框交给共享预览`)
    assert.match(source, /PdfPreviewDialog|usePdfEvidencePreview|PdfEvidencePreviewHost/)
  }
})

test('同形态：审查结果/要求确认/写标/串标都复用同一 pdf.js 预览，不按实体分叉', () => {
  const hosts = [
    'pages/result/ResultDetail.tsx',
    'pages/RequirementReview/index.tsx',
    'pages/writing/WritingClauses.tsx',
    'pages/writing/WritingMaterials/index.tsx',
    'pages/collusionResult/PairDetail.tsx',
  ]

  for (const rel of hosts) {
    const source = readSrc(rel)
    assert.match(
      source,
      /PdfPreviewDialog|usePdfEvidencePreview|PdfEvidencePreviewHost/,
      `${rel} 必须复用共享 PdfPreviewDialog，而不是自建原生查看器`,
    )
    assert.doesNotMatch(
      source,
      /<iframe[^>]*PDF/,
      `${rel} 不得自行 iframe 预览 PDF`,
    )
  }
})

test('软件栅格化预览自备顶栏与缩略图侧栏，不依赖原生插件 chrome', () => {
  const dialog = readSrc('pages/result/PdfPreviewDialog.tsx')
  const chrome = readSrc('pages/result/pdfPreview/PdfPreviewChrome.tsx')

  assert.match(dialog, /PdfPreviewToolbar/)
  assert.match(dialog, /PdfPreviewThumbRail/)
  assert.match(chrome, /from 'react-pdf'/)
  assert.match(chrome, /<Thumbnail/)
  assert.match(chrome, /下载/)
  assert.match(chrome, /缩小/)
  assert.match(chrome, /放大/)
  assert.match(chrome, /页面缩略图/)
  assert.doesNotMatch(chrome, /<iframe/)
  assert.doesNotMatch(chrome, /<embed/)
})

test('预览页码指针统一：画布随 pageNumber 重建，侧栏按挂载后选中页滚入', () => {
  const dialog = readSrc('pages/result/PdfPreviewDialog.tsx')
  const chrome = readSrc('pages/result/pdfPreview/PdfPreviewChrome.tsx')

  assert.match(dialog, /key=\{pageNumber\}/)
  assert.match(dialog, /resolveWheelPageTurn/)
  assert.match(dialog, /addEventListener\('wheel'/)
  assert.match(chrome, /computeRailScrollTop/)
  assert.match(chrome, /\[pageNumber, numPages\]/)
  assert.doesNotMatch(chrome, /scrollIntoView/)
})

test('生产运行时把 worker 收成 JS MIME 路径，并声明 cmap/wasm/字体', () => {
  const runtime = readSrc('pages/result/pdfPreview/pdfjsRuntime.ts')
  const viteConfig = readFrontend('vite.config.ts')
  const plugin = readFrontend('vite-plugin-pdfjs-assets.ts')
  const nginx = readFileSync(join(frontendRoot, '../nginx/nginx.conf'), 'utf8')

  assert.match(runtime, /pdfjs\/pdf\.worker\.min\.js/)
  assert.match(runtime, /cMapUrl/)
  assert.match(runtime, /wasmUrl/)
  assert.match(runtime, /standardFontDataUrl/)
  assert.match(viteConfig, /pdfjsStaticAssets/)
  assert.match(plugin, /pdf\.worker\.min\.js/)
  assert.match(plugin, /cmaps/)
  assert.match(plugin, /wasm/)
  assert.match(plugin, /standard_fonts/)
  assert.match(plugin, /application\/javascript/)
  assert.match(nginx, /application\/javascript mjs/)
})

test('同形态：构建拷贝同时给出 worker.js 与 cmap/wasm，而不是只带入口脚本', () => {
  const dir = mkdtempSync(join(tmpdir(), 'pdfjs-assets-'))
  try {
    copyPdfjsAssetsTo(dir)
    assert.equal(existsSync(join(dir, 'pdfjs', 'pdf.worker.min.js')), true)
    assert.equal(existsSync(join(dir, 'cmaps')), true)
    assert.equal(existsSync(join(dir, 'wasm', 'openjpeg.wasm')), true)
    assert.equal(existsSync(join(dir, 'standard_fonts')), true)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})
