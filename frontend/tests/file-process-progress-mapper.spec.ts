import assert from 'node:assert/strict'
import test from 'node:test'

import { mapProcessStatusForDisplay } from '../src/components/fileProcessProgressMapper.ts'

test('VL阶段应展示已处理进度, 不把成功识别数0误写成阶段推进0', () => {
  const result = mapProcessStatusForDisplay({
    status: 'processing',
    stage: 'vl_recognition',
    stage_label: 'VL图片识别',
    progress: 0,
    total: 1052,
    recognized: 0,
    attempted: 667,
    vl_phase: 'round1_running',
    vl_round_done: 667,
    vl_round_total: 1052,
    stages: [
      { key: 'parsing', label: 'PDF解析', status: 'completed', progress: 1, total: 1 },
      { key: 'vl_recognition', label: 'VL图片识别', status: 'processing', progress: 0, total: 1052 },
      { key: 'chunking', label: '文本分块', status: 'pending', progress: 0, total: 0 },
      { key: 'vectorizing', label: '向量化存储', status: 'pending', progress: 0, total: 0 },
    ],
  })

  const vlStep = result.steps.find((step) => step.id === 'vl_recognition')
  assert.equal(vlStep?.detail, '已处理 667/1052，成功识别 0')
  assert.equal(result.stageText, '步骤2 VL图片识别 · 已处理 667/1052，成功识别 0')
})

test('vl_skipped 为真时省略 VL 图片识别步', () => {
  const result = mapProcessStatusForDisplay({
    status: 'processing',
    stage: 'parsing',
    stage_label: 'PDF解析',
    progress: 0,
    total: 1,
    vl_skipped: true,
    stages: [
      { key: 'parsing', label: 'PDF解析', status: 'processing', progress: 0, total: 1 },
      { key: 'vl_recognition', label: 'VL图片识别', status: 'pending', progress: 0, total: 0 },
      { key: 'chunking', label: '文本分块', status: 'pending', progress: 0, total: 0 },
      { key: 'vectorizing', label: '向量化存储', status: 'pending', progress: 0, total: 0 },
    ],
  })

  assert.equal(result.steps.some((step) => step.id === 'vl_recognition'), false)
  assert.equal(result.steps[0]?.id, 'parsing')
})

// 夹具 1: vectorizing 步骤在 degraded_to_cpu=true 时追加可读降级徽标。
test('vectorizing 步骤在 degraded_to_cpu=true 时追加 CPU 降级运行中文案', () => {
  const result = mapProcessStatusForDisplay({
    status: 'processing',
    stage: 'vectorizing',
    stage_label: '向量化存储',
    progress: 100,
    total: 200,
    degraded_to_cpu: true,
    fallback_reason: 'gpu_admission_insufficient_vram+repeated_admission_fail',
    stages: [
      { key: 'parsing', label: 'PDF解析', status: 'completed', progress: 1, total: 1 },
      { key: 'vl_recognition', label: 'VL图片识别', status: 'completed', progress: 1, total: 1 },
      { key: 'chunking', label: '文本分块', status: 'completed', progress: 1, total: 1 },
      { key: 'vectorizing', label: '向量化存储', status: 'processing', progress: 100, total: 200 },
    ],
  })

  const vecStep = result.steps.find((step) => step.id === 'vectorizing')
  assert.equal(vecStep?.detail, '100/200 · CPU降级运行中 (显存不足)')
  assert.match(result.stageText, /CPU降级运行中/)
})

// 夹具 2: chunking 步骤优先显示后端透传的 detail (子步骤文案),
// 而不是默认空 numeric 进度或 "处理中..."。
test('chunking 步骤透传后端 stage detail 而非 numeric 进度', () => {
  const result = mapProcessStatusForDisplay({
    status: 'processing',
    stage: 'chunking',
    stage_label: '文本分块',
    progress: 0,
    total: 0,
    stages: [
      { key: 'parsing', label: 'PDF解析', status: 'completed', progress: 1, total: 1 },
      { key: 'vl_recognition', label: 'VL图片识别', status: 'completed', progress: 1, total: 1 },
      { key: 'chunking', label: '文本分块', status: 'processing', progress: 0, total: 0, detail: '目录解析完成' },
      { key: 'vectorizing', label: '向量化存储', status: 'pending', progress: 0, total: 0 },
    ],
  })

  const chunkStep = result.steps.find((step) => step.id === 'chunking')
  assert.equal(chunkStep?.detail, '目录解析完成')
  assert.match(result.stageText, /目录解析完成/)
})
