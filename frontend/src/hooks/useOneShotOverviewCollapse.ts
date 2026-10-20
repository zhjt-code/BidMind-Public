/**
 * 职责: 结果页概览展开时监听三栏交互并收起；自动态下另监听内滑到底一次收起。
 * 不做: 不改三栏布局、不持久化、不处理引导 tour。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  INITIAL_OVERVIEW_COLLAPSE_STATE,
  OVERVIEW_BOTTOM_DWELL_MS,
  canConsumeInnerScroll,
  reduceOverviewCollapse,
  resolveDetailShell,
} from '@/lib/overviewCollapse'

export function useOneShotOverviewCollapse() {
  const [state, setState] = useState(INITIAL_OVERVIEW_COLLAPSE_STATE)
  const bodyRef = useRef<HTMLDivElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const dwellTimerRef = useRef<number | null>(null)

  const toggleCollapsed = useCallback(() => {
    setState(prev => reduceOverviewCollapse(prev, { type: 'toggle' }))
  }, [])

  useEffect(() => {
    if (state.collapsed) return

    const root = rootRef.current
    const body = bodyRef.current
    if (!root) return

    const detailShell = resolveDetailShell(root)
    const detailEl = detailShell instanceof HTMLElement ? detailShell : null
    const listenInnerScroll = state.control === 'auto'

    const clearDwell = () => {
      if (dwellTimerRef.current !== null) {
        window.clearTimeout(dwellTimerRef.current)
        dwellTimerRef.current = null
      }
    }

    const consume = (type: 'detail_engage' | 'inner_bottom_dwell') => {
      clearDwell()
      setState(prev => reduceOverviewCollapse(prev, { type }))
    }

    const onBodyScroll = () => {
      if (!body || !canConsumeInnerScroll(body)) {
        clearDwell()
        return
      }
      if (dwellTimerRef.current !== null) return
      dwellTimerRef.current = window.setTimeout(() => {
        dwellTimerRef.current = null
        consume('inner_bottom_dwell')
      }, OVERVIEW_BOTTOM_DWELL_MS)
    }

    const onDetailEngage = () => {
      consume('detail_engage')
    }

    if (listenInnerScroll) {
      body?.addEventListener('scroll', onBodyScroll, { passive: true })
    }
    detailEl?.addEventListener('pointerdown', onDetailEngage, { capture: true })
    detailEl?.addEventListener('wheel', onDetailEngage, { capture: true, passive: true })

    return () => {
      clearDwell()
      if (listenInnerScroll) {
        body?.removeEventListener('scroll', onBodyScroll)
      }
      detailEl?.removeEventListener('pointerdown', onDetailEngage, true)
      detailEl?.removeEventListener('wheel', onDetailEngage, true)
    }
  }, [state.control, state.collapsed])

  return {
    collapsed: state.collapsed,
    toggleCollapsed,
    bodyRef,
    rootRef,
  }
}
