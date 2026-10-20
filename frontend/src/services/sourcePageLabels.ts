/**
 * 全系统溯源页码 UI 文本统一规范工具。
 *
 * 设计目标：
 * - 投标审查 / 标书编写等结构化整数页码 → 统一为 `P{n}` 或 `P{a}-P{b}`
 * - 串标审查 LLM 自由字符串（含"第N页《章节》"等章节锚点）→
 *   提取页号规范化为 `P{n}`，章节信息以 ` · 剩余` 形式保留
 *
 * 不在此处承担 UI 渲染职责，仅做文本格式化，便于单元测试与跨页面复用。
 */

const SEMANTIC_NULLS = new Set([
  '无', 'null', 'none', 'n/a', 'na', '不适用', '未找到',
  '/', '-', '--', '——',
])

/**
 * 生成结构化页码的统一标签：
 * - 单页：`P9`
 * - 跨页：`P9-P10`（两侧都带 P，避免出现 `P9-10` 这种半带 P 的旧写法）
 * - 起始页缺失：返回空串（调用方自行决定显示占位）
 */
export function formatPageRangeLabel(
  start: number | null | undefined,
  end?: number | null | undefined,
): string {
  if (start == null || !Number.isFinite(start) || start <= 0) return ''
  const safeEnd =
    end != null && Number.isFinite(end) && end > 0 && end !== start ? end : null
  if (safeEnd == null) return `P${start}`
  return `P${start}-P${safeEnd}`
}

/**
 * 规范化串标 LLM 自由字符串中的位置描述。
 *
 * 输入示例 → 输出：
 * - "第4页"           → { label: "P4",      extra: null,           page: 4,  endPage: null }
 * - "P5"              → { label: "P5",      extra: null,           page: 5,  endPage: null }
 * - "5"               → { label: "P5",      extra: null,           page: 5,  endPage: null }
 * - "第4-5页"         → { label: "P4-P5",   extra: null,           page: 4,  endPage: 5    }
 * - "第4页《施工资质》章节"
 *                     → { label: "P4 ·《施工资质》章节", extra: "《施工资质》章节", page: 4, endPage: null }
 * - "无" / "" / null   → { label: "",       extra: null,           page: null, endPage: null }
 * - "资质章节"         → { label: "资质章节", extra: "资质章节",       page: null, endPage: null }
 *   （无页号则原样保留为章节文本，不丢失语义）
 *
 * 注意：仅负责格式化，不验证页号是否为真实页码。
 */
export function normalizeLocationLabel(
  location: string | null | undefined,
): {
  label: string
  extra: string | null
  page: number | null
  endPage: number | null
} {
  const empty = { label: '', extra: null, page: null, endPage: null }
  if (!location) return empty
  const raw = location.trim()
  if (!raw) return empty
  if (SEMANTIC_NULLS.has(raw.toLowerCase())) return empty

  let page: number | null = null
  let endPage: number | null = null

  const rangeMatch = raw.match(/(\d+)\s*[\-~–至到]\s*(\d+)/)
  if (rangeMatch) {
    const a = parseInt(rangeMatch[1], 10)
    const b = parseInt(rangeMatch[2], 10)
    if (Number.isFinite(a) && Number.isFinite(b) && a > 0 && b > 0) {
      page = a
      endPage = b !== a ? b : null
    }
  }

  if (page == null) {
    const numMatch = raw.match(/\d+/)
    if (numMatch) page = parseInt(numMatch[0], 10)
  }

  let extra = raw
  if (page != null) {
    const matched = endPage != null
      ? extra.match(/(\d+)\s*[\-~–至到]\s*(\d+)/)
      : extra.match(/\d+/)
    if (matched && matched.index != null) {
      extra = extra.slice(0, matched.index) + extra.slice(matched.index + matched[0].length)
    }
  }
  extra = extra
    .replace(/^\s*(?:第|page|p\.?|页码|位置)\s*[:：]?\s*/i, '')
    .replace(/^\s*页\s*/, '')
    .replace(/^[\s,，;；·\-—:：、]+/, '')
    .replace(/[\s,，;；·\-—:：、]+$/, '')
    .trim()

  if (page != null) {
    const pageLabel = endPage != null ? `P${page}-P${endPage}` : `P${page}`
    if (extra) return { label: `${pageLabel} · ${extra}`, extra, page, endPage }
    return { label: pageLabel, extra: null, page, endPage }
  }
  return { label: extra, extra: extra || null, page: null, endPage: null }
}
