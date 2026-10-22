import React from 'react'

/**
 * 全系统溯源码统一展示组件。
 *
 * 设计原则：
 * - 视觉一致：所有溯源码统一为 "P+数字" 文本，不再夹杂图标 / 裸数字 / 旧式 "第X页"
 * - 行为一致：可点击时一致采用 primary 主题文字按钮 + 同一悬浮态；不可点击降级为灰色文字
 * - 文本前缀由调用方决定：例如 `P9`、`P9-P10`、`P4 · 《施工资质》章节`，本组件不强制截断
 *
 * 该组件不承担"页码 → 文本"的格式化职责。请使用：
 * - `formatPageRangeLabel(start, end)` 生成结构化页码标签
 * - `normalizeLocationLabel(rawString)` 规范化串标 LLM 自由字符串
 */

export interface SourcePageBadgeProps {
  /** 已格式化的展示文本（如 `P9`、`P4 · 《施工资质》章节`）；空串则不渲染。 */
  label: string
  /** 点击触发跳转 PDF；不传则视为不可点击。 */
  onClick?: (event: React.MouseEvent<HTMLButtonElement>) => void
  /** 鼠标悬浮提示文案（优先于自动生成的兜底提示）。 */
  title?: string
  /**
   * 视觉密度：
   * - `inline`（默认）：与正文段落紧贴的文字按钮
   * - `pill`：带边框的胶囊样式，用于弹窗等独立强调场景
   */
  variant?: 'inline' | 'pill'
  /** 透出 className 用于微调字号或间距，不影响主题色与悬浮态。 */
  className?: string
}

const INLINE_CLICKABLE =
  'text-xs text-primary hover:underline font-medium px-1 py-0.5 rounded ' +
  'hover:bg-primary/10 transition-colors'
const INLINE_DISABLED = 'text-xs text-gray-500 px-1 py-0.5'
const PILL_CLICKABLE =
  'inline-flex items-center px-2.5 py-1 rounded border border-primary/30 ' +
  'text-primary text-sm hover:bg-primary/10 transition-colors'
const PILL_DISABLED =
  'inline-flex items-center px-2.5 py-1 rounded border border-gray-200 text-gray-500 text-sm'

const SourcePageBadge: React.FC<SourcePageBadgeProps> = ({
  label,
  onClick,
  title,
  variant = 'inline',
  className,
}) => {
  if (!label) return null

  const clickable = typeof onClick === 'function'
  const baseClass =
    variant === 'pill'
      ? clickable ? PILL_CLICKABLE : PILL_DISABLED
      : clickable ? INLINE_CLICKABLE : INLINE_DISABLED
  const finalClass = className ? `${baseClass} ${className}` : baseClass

  if (clickable) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={finalClass}
        title={title || `查看${label}原文`}
      >
        {label}
      </button>
    )
  }
  return (
    <span className={finalClass} title={title || '原文档未就绪，无法跳转预览'}>
      {label}
    </span>
  )
}

export default SourcePageBadge
