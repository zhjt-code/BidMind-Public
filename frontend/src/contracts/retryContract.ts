/**
 * 职责: 定义"重试/失败"领域的形态字段，让前端零改动适配任何节点/任何失败
 * 不做: 不做错误码枚举、不做本地化文案（文案由后端/词表提供）；不绑业务实体
 */

// ============================================================
// FailureKind -- 故障语义分类（按形态，不按业务实体）
// ============================================================
export type FailureKind =
  | 'transient'   // 瞬时：网络/超时/资源争抢 → 可自动重试
  | 'business'    // 业务：参数/文件损坏/不合规 → 需用户介入
  | 'dependency'  // 依赖：下游服务/磁盘/向量库 → 等待或切换
  | 'fatal'       // 致命：内存/模型崩溃 → 必须人工

// ============================================================
// RecoveryActionKind -- 恢复动作分级
// ============================================================
export type RecoveryActionKind =
  | 'primary'        // 主路径：继续运行
  | 'secondary'      // 次路径：跳过/重置子范围
  | 'destructive'    // 破坏性：取消任务
  | 'informational'  // 信息性：查看日志/复制错误码

// ============================================================
// RetryContext -- 重试上下文（形态字段，不绑具体业务实体）
// ============================================================
export interface RetryContext {
  /** 节点编码（document_process / writing_compose...） */
  scope: string
  /** 已本地化的展示名（由后端给，避免前端 if-else 硬编码） */
  scopeLabel: string
  /** 处理对象 ID */
  targetId: number | null
  /** 处理对象类型 */
  targetKind: 'document' | 'chapter' | 'project' | 'job' | string
  /** 后端可选提供（如 "招标文件-主体.pdf"） */
  targetLabel?: string
  /** 当前阶段码 */
  stage: string | null
  /** 已本地化的阶段名 */
  stageLabel: string | null
  /** 第 N 次尝试 */
  attempt: number
  /** 上限，用于 "01 / ∞" 或 "01 / 03" 展示 */
  maxAttempts?: number
}

// ============================================================
// RetryFailure -- 故障诊断
// ============================================================
export interface RetryFailure {
  kind: FailureKind
  /** 机器可读错误码（FMT_TABLE_DAMAGED），用于精确诊断与客服沟通 */
  code: string | null
  /** 短标题（"PDF 第 14 页表格结构损坏"） */
  title: string
  /** 完整描述 */
  detail: string
  /** 引导性建议（"可上传备用文件"） */
  hint?: string
  /** 是否可自动重试 */
  autoRetryable: boolean
  /** 自动重试倒计时（ms） */
  cooldownMs?: number
}

// ============================================================
// RetryAction -- 可恢复动作
// ============================================================
export interface RetryAction {
  id: string
  label: string
  kind: RecoveryActionKind
  /** 是否需要二次确认弹窗 */
  requiresConfirm?: boolean
  /** 防抖：用户刚点过不再响应 */
  cooldownMs?: number
  /** 键盘提示（如 "↵" "ESC"） */
  shortcut?: string
}

// ============================================================
// RetryContract -- 组件入参契约
// ============================================================
export interface RetryContract {
  context: RetryContext
  failure: RetryFailure
  actions: RetryAction[]
  preservedOutputs: Array<{ id: string; label: string }>
  resetScope: Array<{ id: string; label: string }>
  retryRunning: boolean
  /** ISO 时间戳，用于 "running · 3m 24s" 展示 */
  startedAt?: string
}

// ============================================================
// FAILURE_THEME -- FailureKind 视觉与交互映射（前端唯一决策点）
// 形态驱动：transient / business / dependency / fatal
// 配色收敛到项目主题状态语义色（蓝=进行中 / 橙=需处理 / 红=已停止），
// 不引入主题外的自定义色；全中文文案，面向普通用户。
// ============================================================
export const FAILURE_THEME: Record<FailureKind, {
  /** 弹窗顶部错误类型名（中文，作为大标题的类型标签） */
  label: string
  /** 一句话定性副标题（告诉用户"发生了什么、要不要紧"） */
  summary: string
  /** 图标圆形背景 Tailwind class */
  iconBg: string
  /** 图标颜色 Tailwind class */
  iconColor: string
}> = {
  transient: {
    label: '临时故障',
    summary: '系统正在自动重试，通常稍后可自行恢复',
    iconBg: 'bg-blue-50',
    iconColor: 'text-primary',
  },
  business: {
    label: '需要处理',
    summary: '文件或内容存在问题，需要您处理后才能继续',
    iconBg: 'bg-amber-50',
    iconColor: 'text-amber-600',
  },
  dependency: {
    label: '等待恢复',
    summary: '依赖的服务暂时不可用，可稍后重试',
    iconBg: 'bg-amber-50',
    iconColor: 'text-amber-600',
  },
  fatal: {
    label: '任务已停止',
    summary: '遇到严重故障，任务已停止，请稍后重试',
    iconBg: 'bg-red-50',
    iconColor: 'text-destructive',
  },
}
