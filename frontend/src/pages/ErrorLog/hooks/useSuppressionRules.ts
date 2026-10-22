import { useCallback, useState } from 'react'
import { toast } from 'sonner'
import { errorLogService } from '@/services/errorLogApi'
import type { ErrorLogItem, SuppressionRule } from '../types'

interface UseSuppressionRulesOptions {
  onReload: () => void
}

export interface UseSuppressionRulesResult {
  suppressTarget: ErrorLogItem | null
  setSuppressTarget: (log: ErrorLogItem | null) => void
  suppressReason: string
  setSuppressReason: (s: string) => void
  suppressSubmitting: boolean
  handleSuppress: () => Promise<void>

  rulesDialogOpen: boolean
  setRulesDialogOpen: (v: boolean) => void
  rules: SuppressionRule[]
  rulesLoading: boolean
  rulesTotal: number
  rulesPage: number
  rulesTotalPages: number
  loadRules: (p?: number) => Promise<void>
  handleOpenRulesDialog: () => void
  handleToggleRule: (ruleId: number, enabled: boolean) => Promise<void>
  handleDeleteRule: (ruleId: number) => Promise<void>

  recomputingFp: boolean
  handleRecomputeFingerprints: () => Promise<void>
}

export function useSuppressionRules(opts: UseSuppressionRulesOptions): UseSuppressionRulesResult {
  const { onReload } = opts

  const [suppressTarget, setSuppressTarget] = useState<ErrorLogItem | null>(null)
  const [suppressReason, setSuppressReason] = useState('')
  const [suppressSubmitting, setSuppressSubmitting] = useState(false)

  const [rulesDialogOpen, setRulesDialogOpen] = useState(false)
  const [rules, setRules] = useState<SuppressionRule[]>([])
  const [rulesLoading, setRulesLoading] = useState(false)
  const [rulesTotal, setRulesTotal] = useState(0)
  const [rulesPage, setRulesPage] = useState(1)
  const [rulesTotalPages, setRulesTotalPages] = useState(0)

  const [recomputingFp, setRecomputingFp] = useState(false)

  const loadRules = useCallback(async (p: number = 1) => {
    setRulesLoading(true)
    try {
      const res = await errorLogService.getSuppressionRules({ page: p, page_size: 10 }) as {
        data?: { items?: SuppressionRule[]; total?: number; total_pages?: number }
      }
      const data = res?.data
      setRules(data?.items || [])
      setRulesTotal(data?.total || 0)
      setRulesTotalPages(data?.total_pages || 0)
      setRulesPage(p)
    } catch {
      setRules([])
    } finally {
      setRulesLoading(false)
    }
  }, [])

  const handleOpenRulesDialog = useCallback(() => {
    setRulesDialogOpen(true)
    loadRules(1)
  }, [loadRules])

  const handleSuppress = useCallback(async () => {
    if (!suppressTarget) return
    setSuppressSubmitting(true)
    try {
      await errorLogService.suppress(suppressTarget.id, { reason: suppressReason.trim() })
      toast.success('抑制规则已创建，后续同类日志不再入库')
      setSuppressTarget(null)
      setSuppressReason('')
    } catch {
      toast.error('创建抑制规则失败，可能已存在相同规则')
    } finally {
      setSuppressSubmitting(false)
    }
  }, [suppressTarget, suppressReason])

  const handleToggleRule = useCallback(async (ruleId: number, enabled: boolean) => {
    try {
      await errorLogService.toggleSuppressionRule(ruleId, { enabled })
      toast.success(enabled ? '规则已启用' : '规则已禁用')
      loadRules(rulesPage)
    } catch {
      toast.error('操作失败')
    }
  }, [loadRules, rulesPage])

  const handleDeleteRule = useCallback(async (ruleId: number) => {
    if (!window.confirm('确认删除此抑制规则？删除后同类日志将恢复入库。')) return
    try {
      await errorLogService.deleteSuppressionRule(ruleId)
      toast.success('抑制规则已删除')
      loadRules(rulesPage)
    } catch {
      toast.error('删除失败')
    }
  }, [loadRules, rulesPage])

  const handleRecomputeFingerprints = useCallback(async () => {
    if (!window.confirm('将重新计算所有日志的消息指纹以改进分组效果，此操作可能需要较长时间。确认执行？')) return
    setRecomputingFp(true)
    try {
      const res = await errorLogService.recomputeFingerprints() as { data?: { updated?: number }; message?: string }
      toast.success(res?.message || `已更新 ${res?.data?.updated || 0} 条日志的指纹`)
      onReload()
    } catch {
      toast.error('指纹重算失败')
    } finally {
      setRecomputingFp(false)
    }
  }, [onReload])

  return {
    suppressTarget, setSuppressTarget,
    suppressReason, setSuppressReason,
    suppressSubmitting, handleSuppress,
    rulesDialogOpen, setRulesDialogOpen,
    rules, rulesLoading, rulesTotal, rulesPage, rulesTotalPages,
    loadRules, handleOpenRulesDialog,
    handleToggleRule, handleDeleteRule,
    recomputingFp, handleRecomputeFingerprints,
  }
}
