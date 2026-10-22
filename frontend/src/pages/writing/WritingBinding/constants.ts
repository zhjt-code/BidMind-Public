/**
 * WritingBinding 模块常量（魔法数字 / 字符串集中处）。
 *
 * 派生公式见 bindingDerivations.ts；此处只放与 polling/UI 直接相关的常量。
 */
import {
  WRITING_LONG_POLL_MAX_ATTEMPTS,
  WRITING_PROGRESS_POLL_INTERVAL_MS,
} from '../progressPolling'

/** 轮询匹配状态的间隔（毫秒） */
export const MATCH_POLL_INTERVAL_MS = WRITING_PROGRESS_POLL_INTERVAL_MS

/** 轮询循环最大次数（1000ms × 1800 = 30min 上限） */
export const MATCH_POLL_MAX_ITER = WRITING_LONG_POLL_MAX_ATTEMPTS

/** 轮询失败容忍窗口：前 N 次失败不退出循环，> N 次后才退出（容忍偶发网络抖动） */
export const MATCH_POLL_TRANSIENT_ERROR_WINDOW = 5
