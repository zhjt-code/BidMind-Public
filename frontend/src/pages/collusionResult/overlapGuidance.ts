/**
 * 职责: 判定串标概览区是否展示"重合明细导航引导", 并提供引导文案。
 * 不做: 不渲染 UI、不感知布局模式(旧版 pair 三栏 / 群组模式)差异。
 */

/** 概览区导航引导文案: 指向右侧详情面板的原文引用与高相似重合片段。 */
export const OVERLAP_GUIDANCE_TEXT =
  '查看重合明细：在下方选择文件对或证据条目，右侧详情面板将展示双方原文引用、高相似重合片段与可跳转页码。'

/** 仅依赖导航相关字段的最小结构, 避免引入 collusionApi 运行时依赖(便于纯函数单测)。 */
interface OverlapNavigablePair {
  findings?: unknown[] | null
  evidenceChunks?: unknown[] | null
}

/**
 * 是否存在"可在右侧详情面板查看的重合明细"。
 * 命中任一条件即返回 true:
 * - 任一文件对含正式证据(findings) -> 可经中栏证据进入右栏原文引用
 * - 任一文件对含高相似重合片段(evidenceChunks) -> 可经右栏概览查看重合片段
 * 全部为空时返回 false, 避免在无可导航内容时给出误导性引导。
 */
export function hasNavigableOverlapDetail(pairs: OverlapNavigablePair[]): boolean {
  return pairs.some(
    (p) => (p.findings?.length ?? 0) > 0 || (p.evidenceChunks?.length ?? 0) > 0,
  )
}
