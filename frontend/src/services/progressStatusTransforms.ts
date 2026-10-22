export interface ProgressStatusStep {
/**
 * 后端下发的稳定步骤标识（如招标要求提取的 extract/consolidate/scoring/finalize）。
 * 用于按 key 绑定步骤，避免按数组下标对位在步骤增减时静默错位。
 * 尚未下发 key 的链路（写标/串标等）保持 undefined，由消费方回退下标。
 */
  key?: string
  label: string
  status: string
  progress: number
  detail?: string
}

/**
 * 统一的 UI 步骤状态枚举。
 *
 * 后端历史上同时存在两套命名：
 * - 文档处理链路使用 `processing` / `pending`
 * - 审查 / 串标 / 写标链路使用 `running` / `waiting`
 *
 * 前端组件只消费下面这套 UI 语义：
 * - `completed`：已完成
 * - `processing`：进行中
 * - `error`：失败
 * - `pending`：等待中
 */
export type NormalizedStepStatus =
  | 'pending'
  | 'processing'
  | 'completed'
  | 'error'

/**
 * 统一的整体状态枚举。
 *
 * 历史字段混用：
 * - `processing` / `running` 都表示“在跑”
 * - `idle` / `pending` / 空值 都表示“未开始”
 */
export type NormalizedOverallStatus =
  | 'idle'
  | 'running'
  | 'completed'
  | 'failed'

/**
 * 将后端返回的步骤状态统一映射为前端 UI 枚举。
 *
 * - `processing` / `running` -> `processing`
 * - `pending` / `waiting` / 未知值 -> `pending`
 * - `failed` / `error` -> `error`
 * - `completed` -> `completed`
 */
export function normalizeStepStatus(
  status: string | null | undefined,
): NormalizedStepStatus {
  const value = String(status ?? '').trim().toLowerCase()
  switch (value) {
    case 'completed':
    case 'done':
    case 'success':
      return 'completed'
    case 'processing':
    case 'running':
    case 'in_progress':
    case 'in-progress':
      return 'processing'
    case 'failed':
    case 'error':
    case 'failure':
      return 'error'
    case 'pending':
    case 'waiting':
    case 'queued':
    case 'idle':
    case '':
    default:
      return 'pending'
  }
}

/**
 * 将后端返回的整体状态统一映射为前端约定值。
 *
 * - `processing` / `running` -> `running`
 * - `idle` / `pending` / 空值 -> `idle`
 * - `failed` -> `failed`
 * - `completed` -> `completed`
 */
export function normalizeOverallStatus(
  status: string | null | undefined,
): NormalizedOverallStatus {
  const value = String(status ?? '').trim().toLowerCase()
  switch (value) {
    case 'completed':
    case 'done':
    case 'success':
      return 'completed'
    case 'failed':
    case 'error':
    case 'failure':
      return 'failed'
    case 'running':
    case 'processing':
    case 'in_progress':
    case 'in-progress':
      return 'running'
    case 'idle':
    case 'pending':
    case 'waiting':
    case 'queued':
    case '':
    default:
      return 'idle'
  }
}

/** 整体状态是否表示任务仍在跑（含刚入队等瞬时态）。 */
export function isRunningOverallStatus(
  status: string | null | undefined,
): boolean {
  return normalizeOverallStatus(status) === 'running'
}

const PROGRESS_TOKEN_LABELS: Record<string, string> = {
  hard_constraint: '合规要求',
  structure_constraint: '结构约束',
  generation_guide: '生成指引',
  render_profile: '格式配置解析',
  industry_profile: '行业画像生成',
  structure_analysis: '结构分析',
  directory_extract: '目录提取',
  retrieve_context: '检索文档内容',
  analyze_dimensions: '多维度并行分析',
  deep_search: '补充分析',
  finalize_pair: '整理结果',
  extracting_hard_constraint: '提取合规要求',
  extracting_structure_constraint: '提取结构约束',
  extracting_generation_guide: '提取生成指引',
  extracting: '提取中',
  dedup: '合并去重',
  saving: '保存结果',
  mandatory_coverage: '高风险条款核验',
  scoring_extract: '评分项提取',
  finalizing: '归并核验',
  vectorizing: '向量化',
  classifying: '约束分类',
  retrieving: '章节裁决',
  materials: '素材关联',
  validating: '一致性校验',
  profiling: '章节画像',
  generating: '目录生成',
  cleaning: '环境准备',
  technical: '技术',
  commercial: '商务',
  qualification: '资格',
  pricing: '报价',
  other: '其他',
}

// A3: 业务结论态枚举 -> 用户态中文映射（与后端 i18n.internal_to_display 同义）。
// 用于清洗 narrativeSummary / synthesisSummary / qualityFlags 等 LLM 自由文本中
// 残留的英文枚举，杜绝 inconclusive / unknown / first 等内部枚举裸出。
const USER_FACING_TOKEN_LABELS: Record<string, string> = {
  // 风险等级
  high: '高风险',
  medium: '中风险',
  low: '低风险',
  none: '无风险',
  inconclusive: '无法判定',
  // 决策态
  clear_no_risk: '未发现串标嫌疑',
  suspicious_found: '发现疑似串标',
  partial_review_needed: '需人工复核',
  analysis_failed: '分析失败',
  decidable: '可裁决',
  needs_review: '需人工复核',
  skipped: '未深度分析',
  failed: '分析失败',
  unreviewed: '未深度分析',
  // 证据级
  first: '一级证据',
  second: '二级证据',
  third: '三级证据',
  // 置信度档位
  certain: '确定',
  probable: '高度可能',
  possible: '可能',
  unknown: '未知',
  // 全局发现类型
  personnel_overlap: '人员重叠',
  company_relation: '企业关联',
  pricing_pattern: '报价异常',
  metadata_cluster: '元数据聚类',
  cross_field_correlation: '跨字段串联',
  // 风险口径一致性
  diverged: '存在分歧',
  consistent: '一致',
  // 投标审查符合度
  fully: '完全符合',
  partially: '部分符合',
  // 审查类别（与后端 DISPLAY_TOKEN_MAP 对齐）
  qualification: '资质要求',
  technical: '技术要求',
  commercial: '商务要求',
  performance: '业绩要求',
  staffing: '人员要求',
  format: '格式要求',
  // 写标 schema 字段名（：一致性检查/覆盖告警偶发拼进用户文案）
  source_clause_ids: '来源约束',
  source_material_ids: '来源素材',
}

const USER_FACING_TOKEN_KEYS = Object.keys(USER_FACING_TOKEN_LABELS).sort(
  (a, b) => b.length - a.length,
)

const TOKEN_KEYS = Object.keys(PROGRESS_TOKEN_LABELS).sort((a, b) => b.length - a.length)

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function sanitizeProgressText(rawText: string | null | undefined): string {
  if (!rawText) {
    return ''
  }

  let text = rawText

  // 移除面向调试的内部参数，避免直接显示给用户。
  text = text.replace(
    /\b(?:pair(?:_id)?|group_index|doc(?:ument)?_?id|project_id|node_id|task_id)\s*[:=]\s*[\w:-]+\b/gi,
    ' ',
  )

  // 串标 pair 常被渲染成 `597:598`，对用户无意义。
  text = text.replace(/\b\d+\s*:\s*\d+\b/g, ' ')

  text = text.replace(
    /\b([a-z_]+)\s*:\s*(\d+\s*\/\s*\d+)\b/gi,
    (full, token: string, ratio: string) => {
      const label = PROGRESS_TOKEN_LABELS[token.toLowerCase()]
      if (!label) {
        return full
      }
      return `${label} ${ratio.replace(/\s+/g, '')}`
    },
  )

  for (const token of TOKEN_KEYS) {
    const label = PROGRESS_TOKEN_LABELS[token]
    text = text.replace(new RegExp(`\\b${escapeRegExp(token)}\\b`, 'gi'), label)
  }

  text = text
    .replace(/\s*[|,，;；]+\s*/g, ' ')
    .replace(/\s*·\s*/g, ' · ')
    .replace(/\s{2,}/g, ' ')
    .replace(/^[\s·,:;，；-]+|[\s·,:;，；-]+$/g, '')

  return text.trim()
}

/**
 * A3: 用户态文案出口闸门 — 用于清洗结果页中 LLM 自由文本字段。
 *
 * 与 sanitizeProgressText 区别：
 * - 不删 `597:598` 这类数字（结果文本中数字常为正常含义，如百分比/页码）
 * - 不归一化标点（保留原始换行与中文标点）
 * - 仅做"内部枚举词 -> 中文"的边界替换 + 清除 pair_id/doc_id 等调试参数
 *
 * 该函数为幂等：对已是中文的文本调用 N 次结果不变。
 *
 * 触发自 ：narrativeSummary / synthesisSummary 等结果文本直通到 UI，
 * 出现 inconclusive / unknown 等英文枚举裸出。后端 i18n 闸门已统一覆盖；
 * 前端再做一层兜底，避免新枚举值在后端尚未补齐映射时复发。
 */
export function sanitizeUserFacingText(rawText: string | null | undefined): string {
  if (!rawText) return ''

  let text = String(rawText)

  // 清除调试参数
  text = text.replace(
    /\b(?:pair(?:_id)?|group_index|doc(?:ument)?_?id|project_id|node_id|task_id)\s*[:=]\s*[\w:-]+\b/gi,
    ' ',
  )

  // 按映射表逐 token 替换（边界匹配，避免误伤 'lower' 中的 'low'）
  for (const token of USER_FACING_TOKEN_KEYS) {
    const label = USER_FACING_TOKEN_LABELS[token]
    if (/^[a-z0-9_]+$/i.test(token)) {
      text = text.replace(
        new RegExp(`(?<![A-Za-z0-9_])${escapeRegExp(token)}(?![A-Za-z0-9_])`, 'gi'),
        label,
      )
    } else {
      text = text.replace(new RegExp(escapeRegExp(token), 'g'), label)
    }
  }

  // LLM 自由文本英文污染清洗（与后端 internal_to_display 同义）：
  // 先页码锚点 pages:5-10 → 第5、10页，再字段赋值/比较中文化，最后移除内部规则引用
  text = text.replace(
    /\bpages\s*[:：]\s*(\d[\d\s,，、-]*)/gi,
    (_m: string, nums: string) =>
      '第' + nums.trim().split(/[\s,，、-]+/).filter(Boolean).join('、') + '页',
  )
  text = text.replace(
    /\bis_mandatory\s*[=:：]\s*(true|false|1|0|yes|no)\b/gi,
    (_m: string, val: string) =>
      /^(true|1|yes)$/i.test(val) ? '属于否决项' : '非否决项',
  )
  text = text.replace(
    /\brelevance_score\s*([=<>≤≥:：]+)\s*(\d+)/gi,
    (_m: string, op: string, num: string) => {
      const labels: Record<string, string> = {
        '<': '低于',
        '<=': '不高于',
        '≤': '不高于',
        '>': '高于',
        '>=': '不低于',
        '≥': '不低于',
      }
      const label = labels[op.trim()] ?? ''
      return label ? `相关性评分${label}${num}` : `相关性评分${num}`
    },
  )
  text = text.replace(
    /(?<![A-Za-z0-9_])score\s*([=<>≤≥:：]+)\s*(\d+)/gi,
    (_m: string, op: string, num: string) => {
      const labels: Record<string, string> = {
        '<': '低于',
        '<=': '不高于',
        '≤': '不高于',
        '>': '高于',
        '>=': '不低于',
        '≥': '不低于',
      }
      const label = labels[op.trim()] ?? ''
      return label ? `评分${label}${num}` : `评分${num}`
    },
  )
  // snake_case=已知枚举 → 先由下方 token 表替换 RHS；此处移除残留 ASCII 赋值式
  text = text.replace(
    /\b[a-z][a-z0-9]*_[a-z0-9_]+\s*[=<>≤≥:：]+\s*[A-Za-z0-9_"']+\b/gi,
    ' ',
  )
  text = text.replace(/[Gg]olden\s*[Rr]ules?\s*\d*/gi, '')
  // 审查失败兜底机器标记（与后端 internal_to_display 同义）
  text = text.replace(/\[?\s*LLM_REVIEW_FAILURE\s*\]?/gi, '')
  // 账本闸门/prompt 诊断串不得进入用户态（与后端 internal_to_display 同义）
  text = text.replace(
    /项目证据账本已登记[\s\S]{0,2000}?本条未发现结论与账本冲突，待复核/g,
    '',
  )
  text = text.replace(/来自要求#\d+(?:、#\d+)*/g, '')
  text = text.replace(/项目证据账本/g, '')
  text = text.replace(/本条未发现结论与账本冲突，待复核/g, '')

  // 保留原换行与标点；仅压缩多余空格
  text = text.replace(/[ \t]{2,}/g, ' ')
  return text.trim()
}

export function sanitizeUserFacingList(
  items: ReadonlyArray<string> | null | undefined,
): string[] {
  if (!items || items.length === 0) return []
  const result: string[] = []
  for (const item of items) {
    const cleaned = sanitizeUserFacingText(item)
    if (cleaned) result.push(cleaned)
  }
  return result
}

export function unwrapProgressStatusPayload(
  response: unknown,
  context: string,
): Record<string, unknown> {
  if (!response || typeof response !== 'object') {
    throw new Error(`${context}响应格式异常`)
  }

  const data = (response as Record<string, unknown>).data
  if (!data || typeof data !== 'object') {
    throw new Error(`${context}响应格式异常`)
  }

  return data as Record<string, unknown>
}

export function mapProgressStatusSteps(rawSteps: unknown): ProgressStatusStep[] {
  if (!Array.isArray(rawSteps)) {
    return []
  }

  return rawSteps.map((step) => {
    const current = (step ?? {}) as Record<string, unknown>
    const rawLabel = current.label as string
    const rawDetail = current.detail as string | undefined
    const rawKey = current.key
    return {
      // 仅在后端确实下发 key 时才带上该字段：未接入 key 的链路(写标/串标/文档处理)
      // 返回形状保持不变，避免影响其消费方与既有契约测试。
      ...(typeof rawKey === 'string' && rawKey ? { key: rawKey } : {}),
      label: sanitizeProgressText(rawLabel) || rawLabel,
      status: current.status as string,
      progress: current.progress as number,
      detail: sanitizeProgressText(rawDetail) || undefined,
    }
  })
}
