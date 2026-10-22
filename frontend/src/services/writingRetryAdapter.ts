/**
 * 职责: 把旧版 WritingRetryState 适配为新契约 RetryContract
 * 不做: 不做网络请求、不修改原 transform；调用方零改动
 *
 * 说明: 这是过渡期的薄适配层。后端契约升级为 RetryContract 后,
 * 调用方直接使用契约类型, 本文件可整体删除
 */
import type { WritingRetryState } from './writingRetryTransforms'
import {
  formatFailureMessage,
  formatRetryStageLabel,
} from '../pages/writing/retryActionDisplay'
import type {
  FailureKind,
  RetryAction,
  RetryContext,
  RetryContract,
  RetryFailure,
} from '../contracts/retryContract'

// ============================================================
// 节点编码 → 本地化展示名（数据驱动，不是逻辑硬编码）
// 后端契约升级后,此词表可整体迁移到后端 locale 文件
// ============================================================
const NODE_LABELS: Record<string, string> = {
  writing_material_process: '素材处理',
  writing_compose_single: '单章生成',
  writing_compose: '全文生成',
  writing_match: '素材匹配',
  writing_outline: '目录生成',
  writing_extract: '约束提取',
  writing_tender_process: '招标文件解析',
  writing_render: '文档渲染',
  document_process: '文档解析',
  requirement_extract: '招标要求提取',
  bid_response_extract: '投标响应提取',
  review_compare: 'AI对比审查',
  review_report: '审查报告生成',
  review_project: '投标审查',
  collusion_extract: '文档信息提取',
  collusion_global: '全局特征分析',
  collusion_compare: '重点文件对比',
  collusion_cluster: '嫌疑群组识别',
  collusion_synthesis: '群组深度研判',
  collusion_report: '串标报告生成',
  collusion_analyze: '串标分析',
}

// ============================================================
// 故障分类启发式（无错误码字段时的临时推断）
// 后端提供 failure.code 后,本函数可改为按错误码前缀匹配
// ============================================================
function inferFailureKind(message: string): FailureKind {
  if (/网络|timeout|超时|抖动|连接|econn|ETIMEDOUT/i.test(message)) return 'transient'
  if (/依赖|下游|服务不可用|磁盘|空间不足|vector|索引/i.test(message)) return 'dependency'
  if (/内存|oom|crash|崩溃|致命|段错误/i.test(message)) return 'fatal'
  return 'business'
}

// ============================================================
// 主入口：适配
// ============================================================
export function adaptLegacyRetryState(state: WritingRetryState): RetryContract {
  const scope = state.failedNode || 'unknown'
  const scopeLabel = NODE_LABELS[scope] || scope
  const emptyReason = !state.lastError || !String(state.lastError).trim()
  const runningWithoutReason = Boolean(state.retryRunning && emptyReason)
  const rawMessage = runningWithoutReason
    ? '任务仍在处理，无需再次重试'
    : formatFailureMessage(state.lastError)
  const kind = runningWithoutReason ? 'transient' : inferFailureKind(rawMessage)

  const context: RetryContext = {
    scope,
    scopeLabel,
    targetId: state.targetId,
    targetKind: 'document',
    stage: state.failedStage,
    stageLabel: state.failedStage ? formatRetryStageLabel(state.failedStage) || null : null,
    attempt: state.lastAttempt || 1,
  }

  const failure: RetryFailure = {
    kind,
    code: null,
    title: runningWithoutReason ? `${scopeLabel}进行中` : `${scopeLabel}失败`,
    detail: rawMessage,
    autoRetryable: kind === 'transient',
    cooldownMs: kind === 'transient' ? 30_000 : undefined,
    hint: kind === 'transient'
      ? '系统会自动重试，您也可以稍后手动重试'
      : kind === 'business'
        ? '请检查上传的文件是否完整，然后重新尝试'
        : kind === 'dependency'
          ? '相关服务恢复后可重新尝试，无需重新上传'
          : '如果多次重试仍失败，请稍后再试或联系客服',
  }

  // 面向普通用户的操作：只保留"任务流程"相关动作，不暴露日志/诊断等后端入口
  const actions: RetryAction[] = state.retryable
    ? [
        {
          id: 'primary-retry',
          label: state.retryActionLabel || '重新尝试',
          kind: 'primary',
        },
        {
          id: 'destructive-cancel',
          label: '稍后处理',
          kind: 'destructive',
        },
      ]
    : [
        {
          id: 'destructive-cancel',
          label: '我知道了',
          kind: 'destructive',
        },
      ]

  return {
    context,
    failure,
    actions,
    preservedOutputs: state.preservedOutputs.map((id) => ({ id, label: id })),
    resetScope: state.resetScope.map((id) => ({ id, label: id })),
    retryRunning: state.retryRunning,
  }
}
