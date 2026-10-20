import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildUploadPlaceholderSteps,
  mapApiProgressSteps,
  mapDocProcessToUploadSteps,
  mapExtractToUploadSteps,
  docProgressToOverall,
  mapStageToProgressSteps,
  MATCH_STEPS,
} from '../src/pages/writing/useWritingProgress.ts'
import { omitSkippedImageRecognitionSteps } from '../src/pages/writing/writingUploadHelpers.ts'
import {
  clearPersistedWritingSkipVl,
  persistWritingSkipVl,
  readPersistedWritingSkipVl,
} from '../src/pages/writing/writingUploadVlState.ts'

function installSessionStorageMock() {
  const store = new Map<string, string>()
  const originalWindow = (globalThis as { window?: unknown }).window
  ;(globalThis as { window?: { sessionStorage: Storage } }).window = {
    sessionStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => { store.set(key, value) },
      removeItem: (key: string) => { store.delete(key) },
      clear: () => { store.clear() },
      key: (index: number) => Array.from(store.keys())[index] ?? null,
      get length() { return store.size },
    } as Storage,
  }

  return () => {
    if (originalWindow === undefined) {
      delete (globalThis as { window?: unknown }).window
      return
    }
    ;(globalThis as { window?: unknown }).window = originalWindow
  }
}

// ── docProgressToOverall ────────────────────────────────────────

test('docProgressToOverall: vl_recognition 阶段在未跳过时映射到 15-40 区间', () => {
  const mid = docProgressToOverall('vl_recognition', 0.5, false)
  assert.ok(mid >= 15 && mid <= 40, `expected 15..40 but got ${mid}`)
})

test('docProgressToOverall: vlSkipped 时 vl_recognition 返回 0', () => {
  assert.equal(docProgressToOverall('vl_recognition', 0.5, true), 0)
})

test('docProgressToOverall: vlSkipped 时 chunking 从 25 开始', () => {
  const val = docProgressToOverall('chunking', 0.5, true)
  assert.ok(val >= 25 && val <= 40, `expected 25..40 but got ${val}`)
})

// ── mapDocProcessToUploadSteps: 未跳过(含 VL 步) ────────────────

test('mapDocProcessToUploadSteps: 未跳过时包含 vl_recognition 步', () => {
  const result = mapDocProcessToUploadSteps('parsing', 0.5, false)
  const ids = result.steps.map(s => s.id)
  assert.ok(ids.includes('vl_recognition'), 'should include vl_recognition step')
  assert.equal(ids.length, 6)
})

test('mapDocProcessToUploadSteps: 未跳过 completed 包含 vl_recognition', () => {
  const result = mapDocProcessToUploadSteps('completed', 0, false)
  const ids = result.steps.map(s => s.id)
  assert.ok(ids.includes('vl_recognition'))
  assert.ok(ids.includes('scoring'))
  assert.equal(result.overallTarget, 0.5)
})

// ── mapDocProcessToUploadSteps: vlSkipped=true ──────────────────

test('mapDocProcessToUploadSteps: vlSkipped 时不包含 vl_recognition 步', () => {
  const result = mapDocProcessToUploadSteps('parsing', 0.5, true)
  const ids = result.steps.map(s => s.id)
  assert.ok(!ids.includes('vl_recognition'), 'should NOT include vl_recognition step')
  assert.equal(ids.length, 5)
})

test('mapDocProcessToUploadSteps: vlSkipped completed 不含 vl_recognition', () => {
  const result = mapDocProcessToUploadSteps('completed', 0, true)
  const ids = result.steps.map(s => s.id)
  assert.ok(!ids.includes('vl_recognition'))
  assert.ok(ids.includes('scoring'))
  assert.equal(result.overallTarget, 0.5)
})

test('mapDocProcessToUploadSteps: vlSkipped chunking 阶段 parsing 已完成', () => {
  const result = mapDocProcessToUploadSteps('chunking', 0.5, true)
  const parsing = result.steps.find(s => s.id === 'parsing')
  assert.equal(parsing?.status, 'completed')
  const chunk = result.steps.find(s => s.id === 'chunk_index')
  assert.equal(chunk?.status, 'processing')
})

test('buildUploadPlaceholderSteps: vlSkipped 时首屏骨架不包含 vl_recognition', () => {
  const ids = buildUploadPlaceholderSteps(true).map((step) => step.id)
  assert.deepEqual(ids, ['parsing', 'chunk_index', 'extract', 'scoring', 'analysis'])
})

test('buildUploadPlaceholderSteps: 未跳过时首屏骨架保留 vl_recognition', () => {
  const ids = buildUploadPlaceholderSteps(false).map((step) => step.id)
  assert.deepEqual(ids, ['parsing', 'vl_recognition', 'chunk_index', 'extract', 'scoring', 'analysis'])
})

// ── mapExtractToUploadSteps: vlSkipped ──────────────────────────

test('mapExtractToUploadSteps: vlSkipped 时不包含 vl_recognition 步', () => {
  const result = mapExtractToUploadSteps('extracting', 30, true)
  const ids = result.steps.map(s => s.id)
  assert.ok(!ids.includes('vl_recognition'))
})

test('mapExtractToUploadSteps: 未跳过时包含 vl_recognition 步', () => {
  const result = mapExtractToUploadSteps('extracting', 30, false)
  const ids = result.steps.map(s => s.id)
  assert.ok(ids.includes('vl_recognition'))
})

test('mapExtractToUploadSteps: completed + vlSkipped 5步全部 completed', () => {
  const result = mapExtractToUploadSteps('completed', 100, true)
  assert.equal(result.steps.length, 5)
  assert.ok(result.steps.every(s => s.status === 'completed'))
  assert.equal(result.overallTarget, 1)
})

test('mapExtractToUploadSteps: completed 未跳过 6步全部 completed', () => {
  const result = mapExtractToUploadSteps('completed', 100, false)
  assert.equal(result.steps.length, 6)
  assert.ok(result.steps.every(s => s.status === 'completed'))
  assert.equal(result.overallTarget, 1)
})

test('skip-vl: 文档处理完成切到约束提取时步骤集合保持 5 步', () => {
  const docDone = mapDocProcessToUploadSteps('completed', 0, true)
  const extracting = mapExtractToUploadSteps('extracting', 65, true)

  assert.deepEqual(
    docDone.steps.map((s) => s.id),
    ['parsing', 'chunk_index', 'extract', 'scoring', 'analysis'],
  )
  assert.deepEqual(
    extracting.steps.map((s) => s.id),
    ['parsing', 'chunk_index', 'extract', 'scoring', 'analysis'],
  )
  assert.equal(extracting.steps.find((s) => s.id === 'extract')?.status, 'processing')
})

test('mapExtractToUploadSteps: scoring_authority 独立驱动评分项提取步', () => {
  const result = mapExtractToUploadSteps('scoring_authority', 85, false)
  const scoring = result.steps.find((s) => s.id === 'scoring')
  const extract = result.steps.find((s) => s.id === 'extract')
  const analysis = result.steps.find((s) => s.id === 'analysis')

  assert.equal(extract?.status, 'completed')
  assert.equal(scoring?.status, 'processing')
  assert.equal(analysis?.status, 'pending')
  assert.equal(result.stageText, '步骤5 评分项提取')
})

test('mapStageToProgressSteps: vectorizing 阶段映射到目录向量化而非约束分类', () => {
  const result = mapStageToProgressSteps(MATCH_STEPS, 'vectorizing', 5)
  const processing = result.steps.find((s) => s.status === 'processing')

  assert.equal(processing?.id, 'vectorizing')
  assert.equal(processing?.name, '目录向量化')
  assert.equal(result.stageText, '步骤1 目录向量化')
})

test('mapApiProgressSteps: 行业画像运行态保持当前步骤与顶部文案一致', () => {
  const result = mapApiProgressSteps([
    { label: '约束提取', status: 'completed', progress: 100 },
    { label: '评分项提取', status: 'completed', progress: 100 },
    { label: '格式配置解析', status: 'completed', progress: 100 },
    { label: '行业画像生成', status: 'running', progress: 100, detail: '等待行业画像模型返回' },
    { label: '结构分析', status: 'waiting', progress: 0 },
  ], 94)

  const processing = result?.steps.find((s) => s.status === 'processing')
  assert.equal(processing?.name, '行业画像生成')
  assert.equal(processing?.detail, '等待行业画像模型返回')
  assert.equal(result?.stageText, '步骤4 行业画像生成 · 等待行业画像模型返回')
})

test('mapApiProgressSteps: 统一上传契约下文档处理阶段保持单一进度流', () => {
  const result = mapApiProgressSteps([
    { label: '文档解析', status: 'completed', progress: 100 },
    { label: '图片识别', status: 'completed', progress: 100 },
    { label: '分块索引', status: 'running', progress: 60, detail: '分块索引中' },
    { label: '约束提取', status: 'waiting', progress: 0 },
    { label: '评分项提取', status: 'waiting', progress: 0 },
    { label: '分析画像', status: 'waiting', progress: 0 },
  ], 46)

  const processing = result?.steps.find((s) => s.status === 'processing')
  assert.equal(processing?.name, '分块索引')
  assert.equal(result?.stageText, '步骤3 分块索引 · 分块索引中')
  assert.equal(result?.steps.length, 6)
})

test('omitSkippedImageRecognitionSteps: 本轮已跳过时去掉图片识别', () => {
  const kept = omitSkippedImageRecognitionSteps(
    [
      { label: '文档解析', status: 'running', progress: 40 },
      { label: '图片识别', status: 'waiting', progress: 0 },
      { label: '分块索引', status: 'waiting', progress: 0 },
    ],
    true,
  )
  assert.deepEqual(kept.map((step) => step.label), ['文档解析', '分块索引'])
})

test('omitSkippedImageRecognitionSteps: 同形态审标 VL 步标签同样省略', () => {
  const kept = omitSkippedImageRecognitionSteps(
    [
      { label: 'PDF解析', status: 'running', progress: 10 },
      { label: 'VL图片识别', status: 'waiting', progress: 0 },
      { label: '文本分块', status: 'waiting', progress: 0 },
    ],
    true,
  )
  assert.deepEqual(kept.map((step) => step.label), ['PDF解析', '文本分块'])
})

test('omitSkippedImageRecognitionSteps: 未跳过时原样保留', () => {
  const steps = [
    { label: '文档解析', status: 'running', progress: 40 },
    { label: '图片识别', status: 'waiting', progress: 0 },
  ]
  assert.equal(omitSkippedImageRecognitionSteps(steps, false), steps)
})

test('mapApiProgressSteps: skip-vl 五步契约不展示图片识别', () => {
  const result = mapApiProgressSteps([
    { label: '文档解析', status: 'running', progress: 40, detail: '文档解析中' },
    { label: '分块索引', status: 'waiting', progress: 0 },
    { label: '约束提取', status: 'waiting', progress: 0 },
    { label: '评分项提取', status: 'waiting', progress: 0 },
    { label: '分析画像', status: 'waiting', progress: 0 },
  ], 10)

  assert.equal(result?.steps.length, 5)
  assert.ok(!result?.steps.some((s) => s.name === '图片识别'))
  assert.equal(result?.stageText, '步骤1 文档解析 · 文档解析中')
})

test('writingUploadVlState: sessionStorage 可持久化并清理 vlSkipped 真值', () => {
  const restoreWindow = installSessionStorageMock()

  try {
    assert.equal(readPersistedWritingSkipVl(531), null)

    persistWritingSkipVl(531, true)
    assert.equal(readPersistedWritingSkipVl(531), true)

    persistWritingSkipVl(531, false)
    assert.equal(readPersistedWritingSkipVl(531), false)

    clearPersistedWritingSkipVl(531)
    assert.equal(readPersistedWritingSkipVl(531), null)
  } finally {
    restoreWindow()
  }
})
