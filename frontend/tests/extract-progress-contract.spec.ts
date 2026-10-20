/**
 * 招标要求提取步骤契约的跨端一致性防御测试。
 *
 * 后端 `backend/app/core/tasks/extract_progress.py` 是步骤/阶段的唯一同源；
 * 前端 `EXTRACTION_STEPS` 与 `EXTRACTION_STAGE_RANGES` 是它的镜像。历史上两侧
 * 各自维护映射，改一侧忘另一侧会让进度条错位或长时间卡在同一步（评分项阶段
 * 被折叠进"保存结果"正是这一漂移的表现）。本测试直接读后端源码做机械比对。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  EXTRACTION_STAGE_RANGES,
  EXTRACTION_STEPS,
} from '../src/pages/RequirementReview/constants.ts'

const backendContractSource = readFileSync(
  new URL('../../backend/app/core/tasks/extract_progress.py', import.meta.url),
  'utf8',
)

interface BackendStep {
  key: string
  label: string
  bands: Array<{ stages: string[]; start: number; end: number }>
}

/** 从后端契约源码解析出步骤定义（key / label / 各 band 的 stage 与区间）。 */
function parseBackendSteps(source: string): BackendStep[] {
  const stepChunks = source.split('ExtractProgressStep(').slice(1)
  return stepChunks.map((chunk) => {
    const key = /key="([^"]+)"/.exec(chunk)?.[1] ?? ''
    const label = /label="([^"]+)"/.exec(chunk)?.[1] ?? ''
    const bands: BackendStep['bands'] = []
    const bandRe = /ExtractStageBand\(\s*\(([\s\S]*?)\),\s*(\d+),\s*(\d+),?\s*\)/g
    let matched: RegExpExecArray | null
    while ((matched = bandRe.exec(chunk)) !== null) {
      bands.push({
        stages: [...matched[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]),
        start: Number(matched[2]),
        end: Number(matched[3]),
      })
    }
    return { key, label, bands }
  })
}

const backendSteps = parseBackendSteps(backendContractSource)

test('后端契约源码可解析出完整的五个步骤（解析器自检）', () => {
  assert.equal(backendSteps.length, 5, '解析结果为空或数量不符时，后续断言会失去意义')
  for (const step of backendSteps) {
    assert.ok(step.key, '步骤 key 解析失败')
    assert.ok(step.label, '步骤 label 解析失败')
    assert.ok(step.bands.length > 0, `步骤 ${step.key} 未解析到任何 stage band`)
  }
})

test('前端步骤 id 与 label 逐一对齐后端契约', () => {
  assert.deepEqual(
    EXTRACTION_STEPS.map((s) => s.id),
    backendSteps.map((s) => s.key),
    '前端步骤 id 必须与后端 steps[].key 同名同序，否则按 key 绑定会落空',
  )
  assert.deepEqual(
    EXTRACTION_STEPS.map((s) => s.name),
    backendSteps.map((s) => s.label),
  )
})

test('前端 stage 兜底映射的步骤下标与区间与后端一致', () => {
  for (const [stage, { idx, range }] of Object.entries(EXTRACTION_STAGE_RANGES)) {
    if (stage === 'completed') continue

    const backendIdx = backendSteps.findIndex((step) =>
      step.bands.some((band) => band.stages.includes(stage)),
    )
    assert.notEqual(backendIdx, -1, `后端契约中不存在 stage: ${stage}`)
    assert.equal(idx, backendIdx, `stage ${stage} 的步骤下标与后端不一致`)

    const backendStep = backendSteps[backendIdx]
    const stepStart = Math.min(...backendStep.bands.map((b) => b.start))
    const stepEnd = Math.max(...backendStep.bands.map((b) => b.end))
    assert.deepEqual(
      range,
      [stepStart, stepEnd],
      `stage ${stage} 的进度区间与后端步骤区间不一致`,
    )
  }
})

test('后端每个步骤都有对应的前端兜底 stage，不存在无法高亮的步骤', () => {
  const mappedIdx = new Set(
    Object.entries(EXTRACTION_STAGE_RANGES)
      .filter(([stage]) => stage !== 'completed')
      .map(([, v]) => v.idx),
  )
  for (let i = 0; i < backendSteps.length; i += 1) {
    assert.ok(
      mappedIdx.has(i),
      `步骤 ${backendSteps[i].key} 在前端无任何 stage 映射，兜底路径下永远不会高亮`,
    )
  }
})

test('completed 的 idx 越界以触发"全部完成"分支', () => {
  assert.equal(EXTRACTION_STAGE_RANGES.completed.idx, EXTRACTION_STEPS.length)
})

test('高风险条款核验是合并前的独立第二步且不与 merging/saving 共用步骤', () => {
  assert.equal(EXTRACTION_STEPS[1].id, 'mandatory_coverage')
  assert.equal(EXTRACTION_STAGE_RANGES.mandatory_coverage.idx, 1)
  assert.ok(
    EXTRACTION_STAGE_RANGES.mandatory_coverage.idx < EXTRACTION_STAGE_RANGES.merging.idx,
    ': 补提项必须进入合并收敛栈，核验步在合并步之前',
  )
  assert.notEqual(
    EXTRACTION_STAGE_RANGES.saving.idx,
    EXTRACTION_STAGE_RANGES.mandatory_coverage.idx,
    '高风险核验一旦与 saving 同步骤，文案会被子完成心跳盖住',
  )
})

test('评分项提取是独立第四步且不与 saving 共用步骤', () => {
  assert.equal(EXTRACTION_STEPS[3].id, 'scoring')
  assert.equal(EXTRACTION_STAGE_RANGES.scoring_extract.idx, 3)
  assert.notEqual(
    EXTRACTION_STAGE_RANGES.saving.idx,
    EXTRACTION_STAGE_RANGES.scoring_extract.idx,
    '评分项阶段一旦与 saving 同步骤，长阶段又会被折叠回"保存结果"',
  )
})
