import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('RichTextEditor 注册章节图示节点扩展', () => {
  const source = readFileSync(new URL('../src/components/RichTextEditor.tsx', import.meta.url), 'utf8')

  assert.match(source, /DiagramFigureExtension/)
  assert.match(source, /SectionDiagram\.configure\(\{ projectId: projectId \?\? null \}\)/)
})

test('SectionDiagram 节点必须消费并保留 data-diagram-id figure 协议', () => {
  const source = readFileSync(
    new URL('../src/components/editor/DiagramFigureExtension.tsx', import.meta.url),
    'utf8',
  )

  assert.match(source, /name:\s*'sectionDiagram'/)
  assert.match(source, /tag:\s*'figure\[data-diagram-id\]'/)
  assert.match(source, /getAttribute\('data-diagram-id'\)/)
  assert.match(source, /'data-diagram-id': String\(attributes\.diagramId\)/)
  assert.match(source, /\['figure', mergeAttributes\(HTMLAttributes\), \['figcaption'/)
})

test('SectionDiagram 编辑态图片加载必须走鉴权 blob 接口', () => {
  const source = readFileSync(
    new URL('../src/components/editor/DiagramFigureExtension.tsx', import.meta.url),
    'utf8',
  )

  assert.match(source, /fetchDiagramBlob\(projectId, diagramId\)/)
  assert.match(source, /URL\.createObjectURL\(blob\)/)
  assert.doesNotMatch(source, /src=\{`?\/api\/writing\/projects/)
})

test('DiagramEditDialog 支持甘特阶段和流程节点编辑后提交重绘', () => {
  const source = readFileSync(
    new URL('../src/components/editor/DiagramEditDialog.tsx', import.meta.url),
    'utf8',
  )

  assert.match(source, /getDiagram\(projectId, diagramId\)/)
  assert.match(source, /payload\.phases = phases\.map/)
  assert.match(source, /duration_days: Number\(phase\.durationDays\)/)
  assert.match(source, /payload\.node_labels = nodes\.map/)
  assert.match(source, /updateDiagram\(projectId, diagramId, payload\)/)
})

test('DiagramEditDialog 暴露图示主题与颜色配置并随重绘提交', () => {
  const source = readFileSync(
    new URL('../src/components/editor/DiagramEditDialog.tsx', import.meta.url),
    'utf8',
  )

  assert.match(source, /DIAGRAM_THEME_OPTIONS/)
  assert.match(source, /setStyle\(/)
  assert.match(source, /primary_color/)
  assert.match(source, /text_color/)
  assert.match(source, /font_scale/)
  assert.match(source, /style,/)
})

test('DiagramEditDialog 必须限制在视口内并保持底部操作可达', () => {
  const source = readFileSync(
    new URL('../src/components/editor/DiagramEditDialog.tsx', import.meta.url),
    'utf8',
  )

  assert.match(source, /max-h-\[calc\(100vh-2rem\)\]/)
  assert.match(source, /flex flex-col overflow-hidden/)
  assert.match(source, /flex-1 min-h-0 overflow-y-auto overscroll-contain/)
  assert.match(source, /shrink-0 border-t/)
})

test('DiagramEditDialog 动态阶段和节点禁止使用 index 作为 React key', () => {
  const source = readFileSync(
    new URL('../src/components/editor/DiagramEditDialog.tsx', import.meta.url),
    'utf8',
  )

  assert.match(source, /key=\{phase\.id\}/)
  assert.match(source, /key=\{node\.id\}/)
  assert.doesNotMatch(source, /key=\{`phase-\$\{index\}`\}/)
  assert.doesNotMatch(source, /key=\{`node-\$\{index\}`\}/)
})

test('SectionDiagram 保存后必须按 content_sha256 重新拉取 PNG', () => {
  const source = readFileSync(
    new URL('../src/components/editor/DiagramFigureExtension.tsx', import.meta.url),
    'utf8',
  )

  assert.match(source, /handleUpdated = async \(title: string, contentSha256: string\)/)
  assert.match(source, /fetchDiagramBlob\(projectId, diagramId, contentSha256\)/)
  assert.match(source, /URL\.createObjectURL\(blob\)/)
})

test('DiagramEditDialog 必须等图刷新后再提示重绘成功', () => {
  const source = readFileSync(
    new URL('../src/components/editor/DiagramEditDialog.tsx', import.meta.url),
    'utf8',
  )

  assert.match(source, /await onUpdated\(updated\.title, updated\.content_sha256\)/)
  const updatedCall = source.indexOf('await onUpdated(updated.title, updated.content_sha256)')
  const toastCall = source.indexOf("toast.success('图表已重绘')")
  assert.ok(updatedCall >= 0 && toastCall > updatedCall)
})

test('writingApi 暴露图示读取、鉴权PNG和更新接口', () => {
  const source = readFileSync(new URL('../src/services/writingApi.ts', import.meta.url), 'utf8')

  assert.match(source, /interface WritingDiagram/)
  assert.match(source, /content_sha256: string/)
  assert.match(source, /getDiagram: async \(projectId: number, diagramId: number\)/)
  assert.match(
    source,
    /fetchDiagramBlob: async \(\s*projectId: number,\s*diagramId: number,\s*contentSha256\?: string,\s*\): Promise<Blob>/,
  )
  assert.match(source, /params: contentSha256 \? \{ v: contentSha256 \} : undefined/)
  assert.match(source, /'Cache-Control': 'no-cache'/)
  assert.match(source, /updateDiagram: async \(/)
  assert.match(source, /\/diagrams\/\$\{diagramId\}\/png/)
})

test('writingApi 图示类型包含建筑向扩展类型', () => {
  const source = readFileSync(new URL('../src/services/writingApi.ts', import.meta.url), 'utf8')

  assert.match(source, /'network' \| 'wbs' \| 'zone_layout'/)
  assert.match(source, /network_nodes\?:/)
  assert.match(source, /wbs_packages\?:/)
  assert.match(source, /zones\?:/)
})

test('writingApi 图示类型包含样式配置契约', () => {
  const source = readFileSync(new URL('../src/services/writingApi.ts', import.meta.url), 'utf8')

  assert.match(source, /interface WritingDiagramStyle/)
  assert.match(source, /theme:\s*'classic_blue' \| 'business_gray' \| 'tech_teal'/)
  assert.match(source, /primary_color\?: string/)
  assert.match(source, /text_color\?: string/)
  assert.match(source, /font_scale\?: 'sm' \| 'md' \| 'lg'/)
  assert.match(source, /style\?: WritingDiagramStyle/)
})


test('编辑器表格样式必须在 ProseMirror 内补齐', () => {
  const source = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8')

  assert.match(source, /\.ProseMirror \.tableWrapper/)
  assert.match(source, /\.ProseMirror table/)
  assert.match(source, /border-collapse: collapse/)
  assert.match(source, /\.ProseMirror th,/)
  assert.match(source, /\.ProseMirror td/)
  assert.match(source, /border: 1px solid hsl\(var\(--border\)\)/)
})
