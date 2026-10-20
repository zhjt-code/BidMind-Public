export interface CollusionAutoStartState {
  canStartAnalysis: boolean
  isSomethingLoading: boolean
  isUploading: boolean
  projectStatus?: string | null
  // 自动启动门禁必须排除"启动中"瞬态。React.StrictMode 与 effect
  // 依赖变动会让纯门禁在同一会话内连续放行多次，叠加 setIsStartingAnalysis 异步
  // 落盘的时间窗形成并发 POST /analyze。该字段保持可选 + 默认 false，不依赖任何
  // 会话级持久哨兵，从而保留 的"返回页面可再次自动流转"语义。
  isStartingAnalysis?: boolean
  // 计费准入 403 不会把项目打成终态；该阻断截断 effect 再次自动
  // POST /analyze。 起取值来自共享 useInsufficientBalanceGate.isBlocked，
  // 会话内刷新/重挂载经 sessionStorage 恢复并继续截断。
  analysisBlockedByBalance?: boolean
}

export { isInsufficientBalanceError } from '../lib/insufficientBalance.ts'

export function shouldAutoStartCollusionAnalysis(state: CollusionAutoStartState): boolean {
  const terminalStatuses = new Set(['reviewing', 'completed', 'failed'])
  return (
    state.canStartAnalysis &&
    !state.isSomethingLoading &&
    !state.isUploading &&
    !state.isStartingAnalysis &&
    !state.analysisBlockedByBalance &&
    !terminalStatuses.has(state.projectStatus || '')
  )
}

// "上传并处理"按钮门禁与业务下限对齐
// 投标份数（已有 + 新上传非失败）必须达到 MIN_BID 才可上传, 避免出现
// "红字提示不足、按钮却亮"的反直觉状态; 用户拖入 1 份就处理完成后,
// canStartAnalysis 永远不满足, 仅是白等一轮处理时间
export interface CollusionUploadGateState {
  isUploading: boolean
  isAnyProcessing: boolean
  totalBidCount: number
  minBid: number
}

export function isUploadAndProcessEnabled(state: CollusionUploadGateState): boolean {
  if (state.isUploading) return false
  if (state.isAnyProcessing) return false
  if (state.totalBidCount < state.minBid) return false
  return true
}
