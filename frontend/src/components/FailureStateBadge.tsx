/**
 * B1: FailureState 状态标签组件。
 *
 * 受 REACT_APP_SHOW_FAILURE_STATE 环境变量控制，
 * 默认 false 时不渲染任何 DOM。
 */

import { Badge } from '@/components/ui/badge'
import {
  type FailureState,
  getFailureStateConfig,
} from '@/types/failureState'

const SHOW_FAILURE_STATE =
  import.meta.env.VITE_SHOW_FAILURE_STATE === 'true'

interface FailureStateBadgeProps {
  state?: FailureState | string
  className?: string
}

export function FailureStateBadge({ state, className }: FailureStateBadgeProps) {
  if (!SHOW_FAILURE_STATE) return null

  const config = getFailureStateConfig(state)
  if (!config) return null

  return (
    <Badge
      variant="outline"
      className={`${config.foreground} ${config.background} border-current/20 ${className ?? ''}`}
    >
      {config.label}
    </Badge>
  )
}
