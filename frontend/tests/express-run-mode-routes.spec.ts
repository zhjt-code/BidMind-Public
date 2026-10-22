import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import { getTargetRoute } from '../src/pages/projectList/types.ts'
import {
  bidReviewNextRouteTarget,
  bidReviewPostProcessPath,
  deriveBidReviewUploadPageState,
  isBidReviewUploadChooserMode,
} from '../src/pages/fileUploadParts/uploadPageState.ts'
import {
  getWritingProjectRoute,
  isWritingExpressPipeline,
} from '../src/pages/writing/writingConstants.ts'
import { isRunModeChooserVisible, isRunModeLocked, normalizeRunMode } from '../src/lib/runMode.ts'
import { isWritingUploadChooserStage } from '../src/pages/writing/writingUploadHelpers.ts'
import {
  EXPRESS_QUALITY_WARNING,
  getExpressRiskLines,
  getPostProcessHint,
  getRunModeFieldHint,
} from '../src/lib/runModeCopy.ts'
import {
  mapPreReviewExpressStatus,
  shouldBootstrapReviewExtract,
} from '../src/pages/reviewProgress/expressReviewStatus.ts'
import { mapWritingExpressStatus } from '../src/pages/writing/writingExpressProgress.ts'

const frontendSrc = join(dirname(fileURLToPath(import.meta.url)), '../src')

function reviewProject(status: string, runMode?: 'guided' | 'express') {
  return {
    id: 7,
    name: '审标项目',
    status,
    projectType: 'bid_review',
    runMode,
    createdAt: '2026-09-13T00:00:00Z',
    updatedAt: '2026-09-13T00:00:00Z',
  }
}

function writingProject(status: string, runMode?: 'guided' | 'express') {
  return {
    id: 8,
    name: '写标项目',
    status,
    projectType: 'bid_writing',
    runMode,
    createdAt: '2026-09-13T00:00:00Z',
    updatedAt: '2026-09-13T00:00:00Z',
  }
}

const parsedSet = {
  tender: { exists: true, parsed: 1 as const },
  bid: { anyDoc: true, complete: true, allParsed: true },
}

test('审标 express：parsed/extracting/extracted 进入进度页，guided 仍进确认页', () => {
  for (const status of ['parsed', 'extracting', 'extracted'] as const) {
    assert.equal(getTargetRoute(reviewProject(status, 'express')), '/project/7/progress')
    assert.equal(getTargetRoute(reviewProject(status, 'guided')), '/project/7/requirements')
    assert.equal(getTargetRoute(reviewProject(status)), '/project/7/requirements')
  }
})

test('写标 express：提取后到撰写中进入编写进度，完成后仍进预览', () => {
  assert.equal(getWritingProjectRoute(8, 'extracted', 'express'), '/project/8/writing/progress')
  assert.equal(getWritingProjectRoute(8, 'outlined', 'express'), '/project/8/writing/progress')
  assert.equal(getWritingProjectRoute(8, 'matched', 'express'), '/project/8/writing/progress')
  assert.equal(getWritingProjectRoute(8, 'writing', 'express'), '/project/8/writing/progress')
  assert.equal(getWritingProjectRoute(8, 'failed', 'express'), '/project/8/writing/progress')
  assert.equal(getWritingProjectRoute(8, 'composed', 'express'), '/project/8/writing/preview')
  assert.equal(getWritingProjectRoute(8, 'extracted', 'guided'), '/project/8/writing/clauses')
  assert.equal(getTargetRoute(writingProject('extracted', 'express')), '/project/8/writing/progress')
  assert.equal(getTargetRoute(writingProject('composed', 'express')), '/project/8/writing/preview')
})

test('写标 express 流水线只覆盖确认闸阶段，完成后允许回看确认页', () => {
  assert.equal(isWritingExpressPipeline('extracted', 'express'), true)
  assert.equal(isWritingExpressPipeline('writing', 'express'), true)
  assert.equal(isWritingExpressPipeline('composed', 'express'), false)
  assert.equal(isWritingExpressPipeline('extracted', 'guided'), false)
})

test('审标上传页 express 重定向到进度而不是要求确认', () => {
  const guided = deriveBidReviewUploadPageState({
    projectStatus: 'parsed',
    ...parsedSet,
    isUploading: false,
    isSomethingLoading: false,
  })
  const express = deriveBidReviewUploadPageState({
    projectStatus: 'parsed',
    ...parsedSet,
    isUploading: false,
    isSomethingLoading: false,
    runMode: 'express',
  })

  assert.equal(guided.targetRoute, 'requirements')
  assert.equal(express.targetRoute, 'progress')
  assert.equal(bidReviewNextRouteTarget('express'), 'progress')
  assert.equal(bidReviewPostProcessPath('7', 'express'), '/project/7/progress')
})

test('提取开始后锁定 run_mode，缺省归一为 guided', () => {
  assert.equal(isRunModeLocked('parsing'), false)
  assert.equal(isRunModeLocked('extracting'), true)
  assert.equal(isRunModeLocked('extracted'), true)
  assert.equal(normalizeRunMode('express'), 'express')
  assert.equal(normalizeRunMode('other'), 'guided')
})

test('处理进度态隐藏推进方式：写标 extracting 与审标 processing 同形态', () => {
  assert.equal(isRunModeChooserVisible(isWritingUploadChooserStage('idle')), true)
  assert.equal(isRunModeChooserVisible(isWritingUploadChooserStage('processing')), false)
  assert.equal(isRunModeChooserVisible(isWritingUploadChooserStage('extracting')), false)
  assert.equal(isRunModeChooserVisible(isWritingUploadChooserStage('already_done')), false)
  assert.equal(isRunModeChooserVisible(isBidReviewUploadChooserMode('upload')), true)
  assert.equal(isRunModeChooserVisible(isBidReviewUploadChooserMode('processing')), false)
  assert.equal(isRunModeChooserVisible(isBidReviewUploadChooserMode('replacing')), false)
})

test('审标与写标风险文案覆盖同一形态：跳过确认、知识库默认、连续计费', () => {
  const review = getExpressRiskLines('bid_review').join('\n')
  const writing = getExpressRiskLines('bid_writing').join('\n')
  assert.match(review, /知识库/)
  assert.match(review, /计费/)
  assert.match(writing, /不上传素材/)
  assert.match(writing, /知识库/)
  assert.match(writing, /计费/)
  assert.match(getPostProcessHint('express', 'bid_review'), /审查进度/)
  assert.match(getPostProcessHint('guided', 'bid_review'), /招标要求确认/)
})

test('推进方式提示始终醒目警告缺少人工确认会降低质量，审标与写标共用', () => {
  const warning = '不建议“一步到位”处理业务，缺少人工确认会明显降低结果质量。'
  assert.equal(EXPRESS_QUALITY_WARNING, warning)
  const guided = getRunModeFieldHint('guided', 'bid_review')
  const reviewExpress = getRunModeFieldHint('express', 'bid_review')
  const writingExpress = getRunModeFieldHint('express', 'bid_writing')
  assert.ok(guided.includes(warning))
  assert.ok(reviewExpress.includes(warning))
  assert.ok(writingExpress.includes(warning))
  assert.match(writingExpress, /不上传素材/)
  const runMode = readFileSync(join(frontendSrc, 'components/upload/RunModeOption.tsx'), 'utf8')
  const knowledge = readFileSync(join(frontendSrc, 'components/KnowledgeUsagePanel.tsx'), 'utf8')
  const collusion = readFileSync(join(frontendSrc, 'pages/CollusionUpload.tsx'), 'utf8')
  assert.match(runMode, /EXPRESS_QUALITY_WARNING/)
  assert.match(runMode, /text-amber-200/)
  assert.doesNotMatch(knowledge, /EXPRESS_QUALITY_WARNING/)
  assert.doesNotMatch(collusion, /一步到位/)
})

test('审标 express 进度在 extracted 时进入按默认确认，不把项目标失败', () => {
  assert.equal(shouldBootstrapReviewExtract('parsed'), true)
  assert.equal(shouldBootstrapReviewExtract('reviewing'), false)
  const mapped = mapPreReviewExpressStatus('extracted', { status: 'completed', overall_progress: 100 })
  assert.equal(mapped.overallStatus, 'running')
  assert.equal(mapped.steps[0].status, 'completed')
  assert.equal(mapped.steps[1].status, 'running')
  assert.equal(mapped.steps[1].label, '按默认确认')
})

test('写标 express 进度在 extracted 时提取已完成并进入目录阶段', () => {
  const mapped = mapWritingExpressStatus('extracted', {
    overallStatus: 'running',
    overallProgress: 20,
    message: '正在按默认确认约束',
    errorMessage: undefined,
    currentDetail: '确认约束',
  })
  assert.equal(mapped.overallStatus, 'running')
  assert.equal(mapped.steps[0].status, 'completed')
  assert.equal(mapped.steps[1].status, 'running')
  assert.equal(mapped.steps[1].label, '生成目录')
})

test('上传页与路由接入一步到位，串标页不出现同义选项', () => {
  const app = readFileSync(join(frontendSrc, 'App.tsx'), 'utf8')
  const fileUpload = readFileSync(join(frontendSrc, 'pages/FileUpload.tsx'), 'utf8')
  const writingUpload = readFileSync(join(frontendSrc, 'pages/writing/WritingUpload.tsx'), 'utf8')
  const bidView = readFileSync(join(frontendSrc, 'pages/fileUploadParts/BidReviewUploadView.tsx'), 'utf8')
  const runMode = readFileSync(join(frontendSrc, 'components/upload/RunModeOption.tsx'), 'utf8')
  const knowledge = readFileSync(join(frontendSrc, 'components/KnowledgeUsagePanel.tsx'), 'utf8')
  const collusion = readFileSync(join(frontendSrc, 'pages/CollusionUpload.tsx'), 'utf8')

  assert.match(app, /writing\/progress/)
  assert.match(fileUpload, /RunModeOption/)
  assert.match(writingUpload, /RunModeOption/)
  assert.match(
    fileUpload,
    /isRunModeChooserVisible\(isBidReviewUploadChooserMode\(uploadPageState\.mode\)\)/,
  )
  assert.match(
    writingUpload,
    /isRunModeChooserVisible\(isWritingUploadChooserStage\(stage\)\)/,
  )
  assert.match(fileUpload, /flex flex-wrap items-center justify-end gap-3/)
  assert.match(writingUpload, /flex flex-wrap items-center justify-end gap-3/)
  assert.ok(
    fileUpload.indexOf('投标审查 — 上传文件') < fileUpload.indexOf('<RunModeOption')
    && fileUpload.indexOf('<RunModeOption') < fileUpload.indexOf('<BidReviewUploadView'),
    '审标：推进方式必须在页头标题行右侧，而不是文件卡片内',
  )
  assert.ok(
    writingUpload.indexOf('标书编写 - 上传招标文件') < writingUpload.indexOf('<RunModeOption'),
    '写标：推进方式必须在页头标题行右侧',
  )
  assert.ok(
    writingUpload.indexOf('<RunModeOption') < writingUpload.indexOf('招标文件 (PDF/Word)'),
    '写标：推进方式不得落在招标文件容器内',
  )
  assert.doesNotMatch(bidView, /runModePanel/)
  assert.doesNotMatch(bidView, /RunModeOption/)
  assert.match(runMode, /relative flex flex-col items-stretch gap-1 sm:items-end/)
  assert.match(knowledge, /relative flex flex-col items-stretch gap-1 sm:items-end/)
  assert.doesNotMatch(collusion, /RunModeOption/)
  assert.doesNotMatch(collusion, /一步到位/)
})
