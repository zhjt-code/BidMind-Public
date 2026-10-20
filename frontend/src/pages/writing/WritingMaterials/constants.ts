/**
 * WritingMaterials 页面常量
 *
 * 全部为编译期常量；纯函数依赖（如 mapBatchStatusToSteps）单独放在 batchStatusMapper.ts。
 */

import type { ProgressStep } from '@/hooks/useProgressAnimation'
import type { ChildGroupKey } from './types'

/** 批处理进度面板四步固定模板（顺序即展示顺序） */
export const BATCH_STEPS: ProgressStep[] = [
  { id: 'parsing', name: '解析文档', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'vl_recognition', name: 'VL图片识别', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'indexing', name: '向量化索引', status: 'pending', progress: 0, targetProgress: 0 },
  { id: 'tagging', name: '打标分类', status: 'pending', progress: 0, targetProgress: 0 },
]

/**
 * 轮询"连续无推进"的最大耐受窗口（毫秒），覆盖 GPU stall + CPU 回退 +
 * image_classify / tagging 高延迟场景。
 *
 * 注意：与 writingUploadRetryPolicy.ts 中同名常量保持一致；本页面历史上
 * 直接 import 该常量并使用，不再独立维护副本。
 */
export { POLL_MAX_NO_ADVANCE_MS } from '../writingUploadRetryPolicy.ts'

/** 素材类型 → 中文标签映射 */
export const MATERIAL_TYPE_LABELS: Record<string, string> = {
  text: '文本',
  image: '图片',
  table: '表格',
  certificate: '证照',
  personnel: '人员资料',
  performance: '业绩资料',
  old_bid_section: '历史标书',
  template_fragment: '模板片段',
}

/** 处理状态徽章配置（label + className） */
export const PROCESSING_STATUS_BADGE: Record<string, { label: string; className: string }> = {
  saved: { label: '待处理', className: 'bg-yellow-100 text-yellow-800 border-yellow-200' },
  processing: { label: '处理中', className: 'bg-blue-100 text-blue-800 border-blue-200' },
  completed: { label: '已完成', className: 'bg-green-100 text-green-800 border-green-200' },
  failed: { label: '处理失败', className: 'bg-red-100 text-red-800 border-red-200' },
}

/** 子素材分组展示顺序 */
export const CHILD_GROUP_ORDER: ChildGroupKey[] = ['mixed', 'text', 'table', 'image', 'ignored']

/** 子素材分组元数据（标题 + 默认是否展开） */
export const CHILD_GROUP_META: Record<ChildGroupKey, { label: string; defaultOpen: boolean }> = {
  mixed: { label: '图文子素材', defaultOpen: true },
  text: { label: '文本子素材', defaultOpen: true },
  table: { label: '表格子素材', defaultOpen: false },
  image: { label: '纯图片子素材', defaultOpen: false },
  ignored: { label: '已忽略', defaultOpen: false },
}
