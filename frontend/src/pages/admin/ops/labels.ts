export const OPS_LAYER_LABELS: Record<string, string> = {
  platform: '平台运行',
  throughput: '任务负载',
  stage: '处理时效',
  quality: '结果质量',
  llm: '模型服务',
  vector: '文档解析',
  stability: '结果稳定性',
  ops: '运营反馈',
  frontend: '前端体验',
}

export const OPS_PILLAR_LABELS: Record<string, string> = {
  quality: '质量',
  reliability: '稳定性',
  efficiency: '效率',
  cost: '成本',
}

export const OPS_BUSINESS_LABELS: Record<string, string> = {
  tender: '招标审查',
  collusion: '串标分析',
  writer: '标书编写',
  process: '文档处理',
  all: '全业务',
}

export const TASK_TYPE_LABELS: Record<string, string> = {
  process: '文档处理',
  extract: '要求提取',
  review: '投标审查',
  analyze: '串标分析',
  collusion: '串标分析',
  outline: '目录生成',
  compose: '章节生成',
  material_process: '素材处理',
  material: '素材处理',
  match: '素材匹配',
  writing_extract: '约束提取',
  writing_clause_vectorize: '约束向量化',
  writing_outline: '目录生成',
  writing_material_process: '素材处理',
  writing_match: '素材匹配',
  writing_compose: '章节生成',
  writing_compose_single: '单章节重生成',
  writing_image_vl: '素材图像识别',
  writing_image_classify: '素材图像分类',
}

// 按 flow 区分的复合映射：同一 task_type 在不同业务流中可能有不同的业务命名。
// 例：writer 流的 extract 业务上叫"约束提取"，而 tender 流的 extract 是"要求提取"。
// 查找优先级：先按 flow 精确匹配，未命中则回退到扁平 TASK_TYPE_LABELS。
export const TASK_TYPE_LABELS_BY_FLOW: Record<string, Record<string, string>> = {
  tender: {
    extract: '要求提取',
    review: '投标审查',
  },
  collusion: {
    analyze: '串标分析',
  },
  writer: {
    extract: '约束提取',
    outline: '目录生成',
    material_process: '素材处理',
    material: '素材处理',
    match: '素材匹配',
    compose: '章节生成',
  },
  process: {
    process: '文档处理',
  },
}

export const OUTCOME_LABELS: Record<string, string> = {
  success: '成功',
  failure: '失败',
  cancelled: '取消',
  timeout: '超时',
  degraded: '降级完成',
  unknown: '未知',
}

export const METRIC_KIND_LABELS: Record<string, string> = {
  counter: '累计值',
  histogram: '分布值',
  gauge: '当前值',
}

export const DIRECTION_LABELS: Record<string, string> = {
  higher_better: '越高越好',
  lower_better: '越低越好',
  neutral: '中性',
}

const METRIC_TITLE_LABELS: Record<string, string> = {
  unresolved_error_total: '未处理错误数',
  critical_error_total: '高优先级错误数',
  redis_ready: 'Redis 连接状态',
  db_ready: '数据库连接状态',
  task_active_gauge: '当前活跃任务数',
  task_completed_total: '已完成任务数',
  task_failed_total: '失败任务数',
  task_queue_wait_seconds_avg: '平均排队等待时长',
  gate_pipeline_duration_p95: '质量门控处理 P95 耗时',
  vector_encode_latency_avg: '向量处理平均耗时',
  llm_latency_p95: '模型调用 P95 耗时',
  gate_verdict_total: '质量门控累计裁决次数',
  writer_gate_reject_total: '标书编写被质量门控拦截次数',
  failure_state_perfect_ratio: '直接完成占比',
  judge_outcome_total: '模型裁决累计次数',
  cove_outcome_total: '交叉复核累计次数',
  llm_calls_total: '模型调用累计次数',
  llm_error_rate: '模型调用异常率',
  llm_retry_total: '模型重试累计次数',
  llm_tokens_prompt_window: '输入 Token 消耗',
  llm_tokens_completion_window: '输出 Token 消耗',
  empty_return_total: '空结果返回累计次数',
  json_repair_total: '结构化结果修复累计次数',
  vector_degrade_to_cpu_ratio: 'CPU 降级占比',
  vector_fallback_total: '向量回退累计次数',
  chunk_clean_ratio: '文本质量合格占比',
  stability_conclusion_rate_avg: '结论稳定率',
  stability_failure_state_rate_avg: '失败状态稳定率',
  stability_evidence_jaccard_avg: '关键证据稳定率',
  stability_explanation_drift_avg: '解释漂移率',
  audit_log_total_window: '关键操作记录数',
  top_error_modules: '高频报错模块数',
}

const METRIC_SUBTITLE_LABELS: Record<string, string> = {
  unresolved_error_total: '当前时间范围内尚未处理的错误数量（仅含 ERROR/CRITICAL，不含 WARNING）',
  critical_error_total: '当前时间范围内的错误与严重错误总量（仅含 ERROR/CRITICAL）',
  redis_ready: '用于判断缓存与任务通道是否可正常访问',
  db_ready: '用于判断核心数据库是否可正常访问',
  task_active_gauge: '正在队列中、执行中或恢复中的任务规模',
  task_completed_total: '当前时间范围内已顺利完成的任务数量',
  task_failed_total: '当前时间范围内执行失败的任务数量',
  task_queue_wait_seconds_avg: '任务从进入队列到被领取的平均等待时长',
  gate_pipeline_duration_p95: '质量门控链路中最慢 5% 请求的处理时长',
  vector_encode_latency_avg: '单次向量处理批次的平均耗时',
  llm_latency_p95: '模型调用中最慢 5% 请求的响应时长',
  gate_verdict_total: '统计质量门控在各业务流程中的累计触发规模',
  writer_gate_reject_total: '统计标书编写流程中被质量门控拦截的累计次数',
  failure_state_perfect_ratio: '结果直接完成、无需降级或人工复核的占比',
  judge_outcome_total: '统计模型裁决机制已执行的累计次数',
  cove_outcome_total: '统计交叉复核机制已执行的累计次数',
  llm_calls_total: '模型服务累计处理的请求次数',
  llm_error_rate: '模型调用中返回错误的比例',
  llm_retry_total: '模型调用因异常触发重试的累计次数',
  llm_tokens_prompt_window: '当前时间范围内输入给模型的 Token 总量',
  llm_tokens_completion_window: '当前时间范围内模型输出的 Token 总量',
  empty_return_total: '模型调用后无有效结果返回的累计次数',
  json_repair_total: '结构化输出被自动修复的累计次数',
  vector_degrade_to_cpu_ratio: '活跃向量任务中退化到 CPU 执行的比例',
  vector_fallback_total: '向量处理链路触发回退方案的累计次数',
  chunk_clean_ratio: '文档切块后文本质量合格的占比',
  stability_conclusion_rate_avg: '同一输入重复执行后结论保持一致的程度',
  stability_failure_state_rate_avg: '同一输入重复执行后失败状态保持一致的程度',
  stability_evidence_jaccard_avg: '同一输入重复执行后关键证据保持一致的程度',
  stability_explanation_drift_avg: '同一输入重复执行后解释文本发生漂移的程度',
  audit_log_total_window: '当前时间范围内记录的关键操作次数',
  top_error_modules: '当前时间范围内最常出现错误的模块数量（仅含 ERROR/CRITICAL）',
}

export function formatTaskTypeLabel(taskType: string, flow?: string): string {
  if (flow) {
    const byFlow = TASK_TYPE_LABELS_BY_FLOW[flow]
    if (byFlow && byFlow[taskType]) return byFlow[taskType]
  }
  return TASK_TYPE_LABELS[taskType] || taskType.replace(/_/g, ' / ')
}

export function formatOutcomeLabel(outcome: string): string {
  return OUTCOME_LABELS[outcome] || outcome
}

export function formatBusinessLabel(business: string): string {
  return OPS_BUSINESS_LABELS[business] || business
}

export function formatLayerLabel(layer: string): string {
  return OPS_LAYER_LABELS[layer] || layer
}

export function formatPillarLabel(pillar: string): string {
  return OPS_PILLAR_LABELS[pillar] || pillar
}

export function formatMetricKindLabel(kind: string): string {
  return METRIC_KIND_LABELS[kind] || kind
}

export function formatDirectionLabel(direction: string): string {
  return DIRECTION_LABELS[direction] || direction
}

export function formatMetricTitle(name: string, description: string): string {
  return METRIC_TITLE_LABELS[name] || description || name
}

export function formatMetricSubtitle(name: string, description: string): string | null {
  return METRIC_SUBTITLE_LABELS[name] || (description && description !== METRIC_TITLE_LABELS[name] ? description : null)
}

export function formatMetricReason(name: string, reason: string): string {
  if (!reason) return ''
  if (reason === '累计指标') return '累计型指标，建议结合趋势图观察变化'
  if (reason === '累计指标, 趋势看 timeseries') return '累计型指标，建议结合趋势图观察变化'
  if (reason === 'ping ok') return 'Redis 连接正常'
  if (reason === 'ping failed') return 'Redis 连接失败'
  if (reason === 'able to query error_logs') return '数据库连接正常'
  if (reason === '窗口累计, 详情见 timeseries') return '当前时间范围内累计值，建议结合趋势图观察变化'
  if (reason === 'top 模块详情见 breakdown') return '具体高频报错模块见下方明细'
  if (reason.startsWith('active=')) {
    const matched = reason.match(/active=(\d+)\s+lost=(\d+)/)
    if (matched) return `活跃任务 ${matched[1]}，失联 ${matched[2]}`
  }
  if (reason.startsWith('avg=')) {
    return `平均耗时 ${reason.replace('avg=', '')}`
  }
  if (reason.startsWith('count=')) {
    const matched = reason.match(/count=(\d+)(?:\s+avg=([\d.]+s))?/)
    if (matched) {
      return matched[2] ? `样本 ${matched[1]}，平均耗时 ${matched[2]}` : `样本 ${matched[1]}`
    }
  }
  if (reason.startsWith('error=')) {
    const matched = reason.match(/error=(\d+)\/total=(\d+)/)
    if (matched) return `异常 ${matched[1]} / 总调用 ${matched[2]}`
  }
  if (reason.startsWith('degraded=')) {
    const matched = reason.match(/degraded=(\d+)\/active=(\d+)/)
    if (matched) return `CPU 降级 ${matched[1]} / 活跃任务 ${matched[2]}`
  }
  if (reason.startsWith('clean=')) {
    const matched = reason.match(/clean=(\d+)\/total=(\d+)/)
    if (matched) return `合格文本块 ${matched[1]} / 总块数 ${matched[2]}`
  }
  if (reason.startsWith('samples=')) {
    const matched = reason.match(/samples=(\d+)/)
    if (matched) return `纳入统计样本 ${matched[1]}`
  }
  if (name === 'failure_state_perfect_ratio') {
    if (reason.includes('暂无失败状态样本')) return '当前时间范围内暂无可统计样本'
    const matched = reason.match(/completed=(\d+)\/total=(\d+)/)
    if (matched) {
      return `直接完成 ${matched[1]} / 总样本 ${matched[2]}`
    }
  }
  return reason
}

export function shouldHideMetricValue(_name: string, reason: string): boolean {
  return reason.includes('暂无')
}
