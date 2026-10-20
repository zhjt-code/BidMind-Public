/**
 * 职责: 结果页视图切换的 ?view= 查询参数解析与序列化
 * 不做: 不读路由、不渲染、不发请求
 */

export type ResultView = 'review' | 'quality'

export const RESULT_VIEW_PARAM = 'view'

/** 解析 ?view= 值; 非法值/缺省一律回退响应审查视图 */
export function parseResultView(raw: string | null | undefined): ResultView {
  return raw === 'quality' ? 'quality' : 'review'
}

/** 序列化视图到查询参数; 默认视图(review)返回 null 表示应移除参数 */
export function serializeResultView(view: ResultView): string | null {
  return view === 'quality' ? 'quality' : null
}
