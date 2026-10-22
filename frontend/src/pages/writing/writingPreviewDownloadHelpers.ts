/**
 * P0.4/P0.5: WritingPreview 下载确认弹窗与 consistency banner 的纯函数 helper。
 *
 * 抽成纯函数便于单元测试, 与 writingPreviewFeedback.ts 保持同类归属风格。
 */

export interface ConsistencyIssueLike {
  severity?: string | null
  message?: string | null
  type?: string | null
}

export const BANNER_CLASS_HIGH = 'bg-rose-50 border-rose-200'
export const BANNER_CLASS_NORMAL = 'bg-amber-50 border-amber-200'

export const DOWNLOAD_BUTTON_LABEL_NORMAL = '确认下载'
export const DOWNLOAD_BUTTON_LABEL_HIGH = '我已知晓，继续下载'

/** 下载确认：引导用户用 Word 大纲级别生成目录（不写入 Word 正文） */
export const DOCX_TOC_GUIDE_TEXT =
  '在 Word/WPS 中，可通过「引用」→「目录」→「自动目录」基于标题级别生成结构化目录。'

/** 下载确认：浏览器下载的 Word 偶发受 Zone.Identifier 影响无法打开时的解除步骤 */
export const DOCX_UNLOCK_GUIDE_TEXT =
  '若下载后无法打开，请右键文件 → 属性 → 勾选「解除锁定」→ 确定。'

export function splitIssuesBySeverity<T extends ConsistencyIssueLike>(
  issues: readonly T[] | null | undefined,
): { high: T[]; other: T[]; info: T[] } {
  if (!issues || issues.length === 0) {
    return { high: [], other: [], info: [] }
  }
  const high: T[] = []
  const other: T[] = []
  const info: T[] = []
  for (const issue of issues) {
    // info 级为"事实告知"(如价格分非正文响应), 从"一般提醒"计数分离, 避免稀释警告公信力
    if (issue?.severity === 'high') high.push(issue)
    else if (issue?.severity === 'info') info.push(issue)
    else other.push(issue)
  }
  return { high, other, info }
}

/**
 * 评分项响应量化摘要:
 * - 所有 scoring_* 都计入 totalIssueCount, 确保评分项检查不会在 high 修复后从横幅消失。
 * - scoring_unresponded 额外统计满分, 供横幅展示直接失分风险。
 */
export function summarizeScoringResponse<T extends ConsistencyIssueLike>(
  issues: readonly T[] | null | undefined,
): {
  totalIssueCount: number
  unrespondedCount: number
  unrespondedScore: number
  weakSupportCount: number
  nonBodyCount: number
} {
  let totalIssueCount = 0
  let unrespondedCount = 0
  let unrespondedScore = 0
  let weakSupportCount = 0
  let nonBodyCount = 0
  for (const issue of issues || []) {
    const type = String(issue?.type || '')
    if (type.startsWith('scoring_')) {
      totalIssueCount += 1
    }
    if (type === 'scoring_unresponded') {
      unrespondedCount += 1
      const m = String(issue?.message || '').match(/满分\s*(\d+(?:\.\d+)?)\s*分/)
      if (m) unrespondedScore += Number(m[1]) || 0
    } else if (type === 'scoring_weak_support') {
      weakSupportCount += 1
    } else if (type === 'scoring_non_body') {
      nonBodyCount += 1
    }
  }
  return {
    totalIssueCount,
    unrespondedCount,
    unrespondedScore,
    weakSupportCount,
    nonBodyCount,
  }
}

export function deriveBannerClassName(highCount: number): string {
  return highCount > 0 ? BANNER_CLASS_HIGH : BANNER_CLASS_NORMAL
}

export function deriveDownloadButtonLabel(highCount: number): string {
  return highCount > 0 ? DOWNLOAD_BUTTON_LABEL_HIGH : DOWNLOAD_BUTTON_LABEL_NORMAL
}

export function shouldAllowBlockingIssuesOnConfirmedDownload(highCount: number): boolean {
  return highCount > 0
}

/**
 * 生成下载弹窗中 highSeverity 提示块展示的 issues 切片 (最多 limit 条) +
 * "还有 N 项"提示的溢出数 (小于等于 limit 时为 0)。
 */
export function computeDownloadSeverityPreview<T extends ConsistencyIssueLike>(
  highIssues: readonly T[],
  limit = 3,
): { preview: T[]; overflowCount: number } {
  const preview = highIssues.slice(0, limit)
  const overflowCount = Math.max(0, highIssues.length - limit)
  return { preview, overflowCount }
}

/**
 * 预览页顶部「重新渲染」「下载 Word」按钮的可用性策略。
 *
 * 后端 `_ensure_preview_ready` 仅放行 composed/formatting/completed 三态，
 * failed 状态属于"用户能进入预览页（守卫故意放行以显示重试面板）但不能再走
 * preview/render/download 链路"的特殊态。前端必须在按钮层禁用并明确引导，
 * 避免用户点击后撞到后端 400 警告（参见 ）。
 */
export interface PreviewActionAvailability {
  canRender: boolean
  canDownload: boolean
  blockedReason: string | null
}

export const PREVIEW_BLOCKED_REASON_FAILED =
  '当前任务处于失败状态，请使用下方"重试"按钮重新触发渲染'

export function derivePreviewActionAvailability(
  projectStatus: string | null | undefined,
): PreviewActionAvailability {
  if (projectStatus === 'failed') {
    return {
      canRender: false,
      canDownload: false,
      blockedReason: PREVIEW_BLOCKED_REASON_FAILED,
    }
  }
  return { canRender: true, canDownload: true, blockedReason: null }
}
