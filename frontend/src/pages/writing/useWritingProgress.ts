/**
 * 标书编写多步骤进度映射工具
 *
 * 将后端 heartbeat 的 current_stage + overall_progress 映射为
 * ProgressPanel 所需的 ProgressStep[] 数组。
 *
 * 各页面只需定义步骤配置 (WritingStageConfig), 轮询时调用
 * mapStageToProgressSteps 即可驱动 ProgressPanel compact。
 */

import type { ProgressStep } from '@/hooks/useProgressAnimation'
import {
  normalizeStepStatus,
  type ProgressStatusStep,
} from '../../services/progressStatusTransforms.ts'

export interface WritingStageStep {
  id: string
  name: string
  /** 该前端步骤涵盖的后端 stage 名列表 */
  stages: string[]
  /** 该步骤在 overall_progress(0-100) 中的起止区间 */
  range: [number, number]
}

export interface WritingStageConfig {
  steps: WritingStageStep[]
}

interface ProgressMappingResult {
  steps: ProgressStep[]
  overallTarget: number
  stageText: string
}

export function mapApiProgressSteps(
  apiSteps: Array<ProgressStatusStep | { label: string; status: string; progress: number; detail?: string }> | undefined,
  overallProgress: number,
  fallbackStageText = '',
): ProgressMappingResult | null {
  if (!apiSteps || apiSteps.length === 0) {
    return null
  }

  const steps: ProgressStep[] = apiSteps.map((step, index) => ({
    id: `api-${index}`,
    name: step.label,
    status: normalizeStepStatus(step.status),
    progress: 0,
    targetProgress: Math.max(0, Math.min((step.progress || 0) / 100, 1)),
    detail: step.detail,
  }))

  const runningStep = steps.find((step) => step.status === 'processing')
  let stageText = fallbackStageText
  if (!stageText && runningStep) {
    const idx = steps.indexOf(runningStep) + 1
    stageText = `步骤${idx} ${runningStep.name}`
    if (runningStep.detail) stageText += ` · ${runningStep.detail}`
  } else if (!stageText && overallProgress >= 100) {
    stageText = '处理完成'
  }

  return {
    steps,
    overallTarget: Math.max(0, Math.min(overallProgress / 100, 1)),
    stageText,
  }
}

/**
 * 将后端 current_stage + overall_progress 映射为 ProgressStep[]
 *
 * 逻辑:
 * 1. 找到 currentStage 所属的步骤 (stepIdx)
 * 2. stepIdx 之前的步骤 -> completed
 * 3. stepIdx 当前步骤 -> processing, 步骤内进度由 overallProgress 在该步骤 range 内反算
 * 4. stepIdx 之后的步骤 -> pending
 * 5. 未知 stage 时用 overallProgress 推断当前所在步骤
 */
export function mapStageToProgressSteps(
  config: WritingStageConfig,
  currentStage: string,
  overallProgress: number,
): ProgressMappingResult {
  const { steps: configSteps } = config
  const isCompleted = currentStage === 'completed'
  const isFailed = currentStage === 'failed'
  const pct = Math.max(0, Math.min(100, overallProgress))

  let activeIdx = -1
  if (isCompleted) {
    activeIdx = configSteps.length
  } else if (!isFailed) {
    activeIdx = configSteps.findIndex((s) => s.stages.includes(currentStage))
    if (activeIdx === -1) {
      // 未知 stage 时根据 overallProgress 推断
      for (let i = configSteps.length - 1; i >= 0; i--) {
        if (pct >= configSteps[i].range[0]) {
          activeIdx = i
          break
        }
      }
      if (activeIdx === -1) activeIdx = 0
    }
  }

  const steps: ProgressStep[] = configSteps.map((cfg, i) => {
    let status: ProgressStep['status'] = 'pending'
    let targetProgress = 0
    let detail: string | undefined

    if (isCompleted || i < activeIdx) {
      status = 'completed'
      targetProgress = 1
    } else if (i === activeIdx) {
      status = isFailed ? 'error' : 'processing'
      const [rangeStart, rangeEnd] = cfg.range
      const rangeSpan = rangeEnd - rangeStart
      if (rangeSpan > 0) {
        targetProgress = Math.max(0, Math.min((pct - rangeStart) / rangeSpan, 0.95))
      }
    }

    return {
      id: cfg.id,
      name: cfg.name,
      status,
      progress: 0,
      targetProgress,
      detail,
    }
  })

  const completedCount = steps.filter((s) => s.status === 'completed').length
  const processingStep = steps.find((s) => s.status === 'processing')
  const processingContrib = processingStep
    ? processingStep.targetProgress / steps.length
    : 0
  const overallTarget =
    isCompleted || completedCount === steps.length
      ? 1
      : Math.min(completedCount / steps.length + processingContrib, 1)

  let stageText = ''
  if (isCompleted) {
    stageText = '处理完成'
  } else if (isFailed) {
    stageText = '处理失败'
  } else if (processingStep) {
    const idx = steps.indexOf(processingStep) + 1
    stageText = `步骤${idx} ${processingStep.name}`
  }

  return { steps, overallTarget, stageText }
}

function createStep(
  id: string,
  name: string,
  status: ProgressStep['status'],
  targetProgress: number,
): ProgressStep {
  return {
    id,
    name,
    status,
    progress: 0,
    targetProgress,
  }
}

function summarizeProgressSteps(
  steps: ProgressStep[],
  fallbackStageText = '',
): ProgressMappingResult {
  const completedCount = steps.filter((s) => s.status === 'completed').length
  const processingStep = steps.find((s) => s.status === 'processing')
  const processingContrib = processingStep
    ? processingStep.targetProgress / steps.length
    : 0
  const overallTarget = completedCount === steps.length
    ? 1
    : Math.min(completedCount / steps.length + processingContrib, 1)

  let stageText = fallbackStageText
  if (!stageText && processingStep) {
    const idx = steps.indexOf(processingStep) + 1
    stageText = `步骤${idx} ${processingStep.name}`
  }

  return { steps, overallTarget, stageText }
}

// ==================== 各任务的步骤配置 ====================

/** 约束提取 (WritingUpload extracting 阶段 / 细粒度步骤) */
export const EXTRACT_STEPS: WritingStageConfig = {
  steps: [
    {
      id: 'extract_constraints',
      name: '约束提取',
      stages: [
        'starting', 'extracting', 'extracting_hard_constraint',
        'extracting_structure_constraint', 'extracting_generation_guide',
        'dedup', 'saving',
      ],
      range: [0, 80],
    },
    {
      id: 'scoring_authority',
      name: '评分项提取',
      stages: ['scoring_authority'],
      range: [80, 90],
    },
    {
      id: 'render_profile',
      name: '格式配置解析',
      stages: ['render_profile'],
      range: [90, 93],
    },
    {
      id: 'industry_profile',
      name: '行业画像生成',
      stages: ['industry_profile'],
      range: [93, 96],
    },
    {
      id: 'structure_analysis',
      name: '结构分析',
      stages: ['structure_analysis', 'directory_extract'],
      range: [96, 100],
    },
  ],
}

/** 目录生成 (WritingOutline) */
export const OUTLINE_STEPS: WritingStageConfig = {
  steps: [
    {
      id: 'cleaning',
      name: '环境准备',
      stages: ['starting', 'cleaning'],
      range: [0, 10],
    },
    {
      id: 'generating',
      name: '目录生成',
      stages: ['generating'],
      range: [10, 90],
    },
    {
      id: 'profiling',
      name: '章节画像',
      stages: ['profiling'],
      range: [90, 100],
    },
  ],
}

/** 上传页完整流程: 文档处理(3步) + 约束提取 + 评分项提取 + 分析画像 = 6步 */
export const UPLOAD_STEPS: WritingStageConfig = {
  steps: [
    {
      id: 'parsing',
      name: '文档解析',
      stages: ['parsing'],
      range: [0, 15],
    },
    {
      id: 'vl_recognition',
      name: '图片识别',
      stages: ['vl_recognition'],
      range: [15, 40],
    },
    {
      id: 'chunk_index',
      name: '分块索引',
      stages: ['chunking', 'vectorizing'],
      range: [40, 50],
    },
    {
      id: 'extract',
      name: '约束提取',
      stages: [
        'starting', 'extracting', 'extracting_hard_constraint',
        'extracting_structure_constraint', 'extracting_generation_guide',
        'dedup', 'saving',
      ],
      range: [50, 90],
    },
    {
      id: 'scoring',
      name: '评分项提取',
      stages: ['scoring_authority'],
      range: [90, 95],
    },
    {
      id: 'analysis',
      name: '分析画像',
      stages: ['render_profile', 'industry_profile', 'structure_analysis', 'directory_extract'],
      range: [95, 100],
    },
  ],
}

const UPLOAD_PLACEHOLDER_STEPS = {
  withVl: [
    ['parsing', '文档解析'],
    ['vl_recognition', '图片识别'],
    ['chunk_index', '分块索引'],
    ['extract', '约束提取'],
    ['scoring', '评分项提取'],
    ['analysis', '分析画像'],
  ],
  skipVl: [
    ['parsing', '文档解析'],
    ['chunk_index', '分块索引'],
    ['extract', '约束提取'],
    ['scoring', '评分项提取'],
    ['analysis', '分析画像'],
  ],
} as const

export function buildUploadPlaceholderSteps(vlSkipped = false): ProgressStep[] {
  const defs = vlSkipped
    ? UPLOAD_PLACEHOLDER_STEPS.skipVl
    : UPLOAD_PLACEHOLDER_STEPS.withVl

  return defs.map(([id, name]) => createStep(id, name, 'pending', 0))
}

/**
 * 将文档处理阶段的 stage + 阶段内进度 换算为 UPLOAD_STEPS 的 overall_progress(0-100)。
 * 文档处理覆盖前 3 步(range 0-50),内部 4 个后端阶段按固定权重分配。
 */
const DOC_STAGE_RANGES: Record<string, [number, number]> = {
  parsing: [0, 15],
  vl_recognition: [15, 40],
  chunking: [40, 45],
  vectorizing: [45, 50],
}

const DOC_STAGE_RANGES_NO_VL: Record<string, [number, number]> = {
  parsing: [0, 25],
  chunking: [25, 40],
  vectorizing: [40, 50],
}

export function docProgressToOverall(docStage: string, stageRatio: number, vlSkipped = false): number {
  const ranges = vlSkipped ? DOC_STAGE_RANGES_NO_VL : DOC_STAGE_RANGES
  const range = ranges[docStage]
  if (!range) return 0
  const [start, end] = range
  return start + (end - start) * Math.max(0, Math.min(1, stageRatio))
}

/**
 * 上传页专用: 文档处理阶段只允许驱动前 3 步。
 * 即使后端返回 completed，也只能表示“文档处理完成”，不能打满 6 步总流程。
 */
export function mapDocProcessToUploadSteps(
  docStage: string,
  stageRatio: number,
  vlSkipped = false,
): ProgressMappingResult {
  const clampedRatio = Math.max(0, Math.min(1, stageRatio))
  const uploadPct = docStage === 'completed'
    ? 50
    : docProgressToOverall(docStage, clampedRatio, vlSkipped)

  const pendingTail: ProgressStep[] = [
    createStep('extract', '约束提取', 'pending', 0),
    createStep('scoring', '评分项提取', 'pending', 0),
    createStep('analysis', '分析画像', 'pending', 0),
  ]

  if (docStage === 'completed') {
    return {
      steps: vlSkipped
        ? [
            createStep('parsing', '文档解析', 'completed', 1),
            createStep('chunk_index', '分块索引', 'completed', 1),
            ...pendingTail,
          ]
        : [
            createStep('parsing', '文档解析', 'completed', 1),
            createStep('vl_recognition', '图片识别', 'completed', 1),
            createStep('chunk_index', '分块索引', 'completed', 1),
            ...pendingTail,
          ],
      overallTarget: 0.5,
      stageText: '文档处理完成，准备提取约束',
    }
  }

  const parsingTarget = Math.max(0, Math.min(uploadPct / (vlSkipped ? 25 : 15), 0.95))
  const chunkTarget = Math.max(0, Math.min((uploadPct - (vlSkipped ? 25 : 40)) / (vlSkipped ? 15 : 10), 0.95))
  const vlTarget = Math.max(0, Math.min((uploadPct - 15) / 25, 0.95))

  let steps: ProgressStep[]

  if (vlSkipped) {
    switch (docStage) {
      case 'chunking':
      case 'vectorizing':
        steps = [
          createStep('parsing', '文档解析', 'completed', 1),
          createStep('chunk_index', '分块索引', 'processing', chunkTarget),
          ...pendingTail,
        ]
        break
      case 'parsing':
      default:
        steps = [
          createStep('parsing', '文档解析', 'processing', parsingTarget),
          createStep('chunk_index', '分块索引', 'pending', 0),
          ...pendingTail,
        ]
        break
    }
  } else {
    switch (docStage) {
      case 'vl_recognition':
        steps = [
          createStep('parsing', '文档解析', 'completed', 1),
          createStep('vl_recognition', '图片识别', 'processing', vlTarget),
          createStep('chunk_index', '分块索引', 'pending', 0),
          ...pendingTail,
        ]
        break
      case 'chunking':
      case 'vectorizing':
        steps = [
          createStep('parsing', '文档解析', 'completed', 1),
          createStep('vl_recognition', '图片识别', 'completed', 1),
          createStep('chunk_index', '分块索引', 'processing', chunkTarget),
          ...pendingTail,
        ]
        break
      case 'parsing':
      default:
        steps = [
          createStep('parsing', '文档解析', 'processing', parsingTarget),
          createStep('vl_recognition', '图片识别', 'pending', 0),
          createStep('chunk_index', '分块索引', 'pending', 0),
          ...pendingTail,
        ]
        break
    }
  }

  const summary = summarizeProgressSteps(steps)
  return {
    ...summary,
    overallTarget: Math.min(uploadPct / 100, 0.5),
  }
}

/**
 * 上传页专用: 约束提取阶段以前 3 步已完成为前提，驱动第 4/5/6 步。
 */
export function mapExtractToUploadSteps(
  currentStage: string,
  extractOverallProgress: number,
  vlSkipped = false,
): ProgressMappingResult {
  const clampedPct = Math.max(0, Math.min(100, extractOverallProgress))
  const uploadPct = 50 + clampedPct * 0.5

  const completedDocSteps = vlSkipped
    ? [
        createStep('parsing', '文档解析', 'completed' as const, 1),
        createStep('chunk_index', '分块索引', 'completed' as const, 1),
      ]
    : [
        createStep('parsing', '文档解析', 'completed' as const, 1),
        createStep('vl_recognition', '图片识别', 'completed' as const, 1),
        createStep('chunk_index', '分块索引', 'completed' as const, 1),
      ]

  if (currentStage === 'completed') {
    return {
      steps: [
        ...completedDocSteps,
        createStep('extract', '约束提取', 'completed', 1),
        createStep('scoring', '评分项提取', 'completed', 1),
        createStep('analysis', '分析画像', 'completed', 1),
      ],
      overallTarget: 1,
      stageText: '处理完成',
    }
  }

  const extractStages = new Set(UPLOAD_STEPS.steps[3].stages)
  const scoringStages = new Set(UPLOAD_STEPS.steps[4].stages)
  const analysisStages = new Set(UPLOAD_STEPS.steps[5].stages)
  const extractTarget = Math.max(0, Math.min(clampedPct / 80, 0.95))
  const scoringTarget = Math.max(0, Math.min((clampedPct - 80) / 10, 0.95))
  const analysisTarget = Math.max(0, Math.min((clampedPct - 90) / 10, 0.95))

  let steps: ProgressStep[]

  if (analysisStages.has(currentStage) || uploadPct >= 95) {
    steps = [
      ...completedDocSteps,
      createStep('extract', '约束提取', 'completed', 1),
      createStep('scoring', '评分项提取', 'completed', 1),
      createStep('analysis', '分析画像', 'processing', analysisTarget),
    ]
  } else if (scoringStages.has(currentStage) || uploadPct >= 90) {
    steps = [
      ...completedDocSteps,
      createStep('extract', '约束提取', 'completed', 1),
      createStep('scoring', '评分项提取', 'processing', scoringTarget),
      createStep('analysis', '分析画像', 'pending', 0),
    ]
  } else if (extractStages.has(currentStage) || currentStage === '' || uploadPct >= 50) {
    steps = [
      ...completedDocSteps,
      createStep('extract', '约束提取', 'processing', extractTarget),
      createStep('scoring', '评分项提取', 'pending', 0),
      createStep('analysis', '分析画像', 'pending', 0),
    ]
  } else {
    steps = [
      ...completedDocSteps,
      createStep('extract', '约束提取', 'pending', 0),
      createStep('scoring', '评分项提取', 'pending', 0),
      createStep('analysis', '分析画像', 'pending', 0),
    ]
  }

  const summary = summarizeProgressSteps(steps)
  return {
    ...summary,
    overallTarget: Math.max(0.5, Math.min(uploadPct / 100, 1)),
  }
}

/** 素材匹配 (WritingBinding) */
export const MATCH_STEPS: WritingStageConfig = {
  steps: [
    {
      id: 'vectorizing',
      name: '目录向量化',
      stages: ['starting', 'matching', 'vectorizing'],
      range: [0, 12],
    },
    {
      id: 'classifying',
      name: '约束分类',
      stages: ['classifying'],
      range: [12, 25],
    },
    {
      id: 'retrieving',
      name: '章节裁决',
      stages: ['retrieving'],
      range: [25, 60],
    },
    {
      id: 'materials',
      name: '素材关联',
      stages: ['materials'],
      range: [60, 80],
    },
    {
      id: 'validating',
      name: '一致性校验',
      stages: ['validating', 'saving'],
      range: [80, 100],
    },
  ],
}
