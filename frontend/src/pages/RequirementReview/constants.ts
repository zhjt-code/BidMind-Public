/**
 * 招标要求确认页（RequirementReview）模块级常量。
 *
 * 单独抽出为 .ts 文件以便 `frontend/tests/` 直接 import 做契约测试，
 * 同时让主 `index.tsx` 仅承担 UI 编排，禁止在主文件内复制常量字典。
 */

import type { ProgressStep } from '../../hooks/useProgressAnimation.ts'

/**
 * 分类排序优先级：资质 > 技术 > 商务 > 业绩 > 人员 > 格式。
 *
 * 同时收录枚举 name (QUAL/TECH/...) 与后端实际返回的小写 value
 * (qualification/technical/...) 两套键，避免单边漏匹配。
 */
export const categoryOrder: Record<string, number> = {
  QUAL: 1,
  TECH: 2,
  COMM: 3,
  PERF: 4,
  STAFF: 5,
  FORMAT: 6,
  qualification: 1,
  technical: 2,
  commercial: 3,
  performance: 4,
  staffing: 5,
  format: 6,
}

/** 添加要求弹窗的分类下拉选项。 */
export const CATEGORY_OPTIONS = [
  { value: 'QUAL', label: '资质要求' },
  { value: 'TECH', label: '技术要求' },
  { value: 'COMM', label: '商务要求' },
  { value: 'PERF', label: '业绩要求' },
  { value: 'STAFF', label: '人员要求' },
  { value: 'FORMAT', label: '格式要求' },
]

/**
 * 提取阶段的进度步骤模板。
 *
 * 步骤 id 必须与后端 `app/core/tasks/extract_progress.EXTRACT_PROGRESS_STEPS`
 * 的 `key` 逐一同名——后端 steps[] 现在下发 key，前端按 key 绑定而非按数组
 * 下标对位（下标对位在步骤增减时会静默错位）。
 *
 * 与后端真实执行顺序的对应:
 * extract -> 分块并行 LLM 抽取
 * mandatory_coverage -> 高风险条款核验（合并前，补提项进入同一收敛栈; ）
 * consolidate -> 合并去重 + 语义压缩/精度保护合并 + 评审意义判定 + 普通要求落库
 * scoring -> 评分项提取（独立长阶段）
 * finalize -> 跨通道归并 + 质量统计 + 终态提交
 */
export const EXTRACTION_STEPS: ProgressStep[] = [
  { id: 'extract', name: '要求提取', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'mandatory_coverage', name: '高风险条款核验', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'consolidate', name: '合并与保存', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'scoring', name: '评分项提取', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'finalize', name: '归并核验', status: 'pending', progress: 0, targetProgress: 0 },
]

/**
 * 后端 stage 字符串到 (步骤索引, 进度区间) 的映射。
 *
 * 仅用于后端未下发 steps[] 时的兜底路径；区间必须与后端
 * `EXTRACT_PROGRESS_STEPS` 各步的 [start, end] 保持一致。
 *
 * `completed` 用 `idx === EXTRACTION_STEPS.length` 表示"全部完成"，
 * 在 mapExtractionToSteps 中触发"全部 completed"分支。
 */
export const EXTRACTION_STAGE_RANGES: Record<string, { idx: number; range: [number, number] }> = {
  extracting: { idx: 0, range: [0, 50] },
  mandatory_coverage: { idx: 1, range: [50, 55] },
  merging: { idx: 2, range: [55, 90] },
  dedup: { idx: 2, range: [55, 90] },
  saving: { idx: 2, range: [55, 90] },
  scoring_extract: { idx: 3, range: [90, 96] },
  finalizing: { idx: 4, range: [96, 100] },
  completed: { idx: 5, range: [100, 100] },
}

/** 单条要求行内联展示的最大来源页码数；超过则触发"全部 N 页"弹窗。 */
export const SOURCE_PAGES_INLINE_LIMIT = 6
