import { useCallback, useState } from 'react'
import { toast } from 'sonner'
import { errorLogService } from '@/services/errorLogApi'
import type { ErrorLogItem } from '../types'

export interface UseErrorChainResult {
  chainIncidentId: string | null
  chainItems: ErrorLogItem[]
  chainLoading: boolean
  openChain: (incidentId: string) => Promise<void>
  closeChain: () => void
}

export function useErrorChain(): UseErrorChainResult {
  const [chainIncidentId, setChainIncidentId] = useState<string | null>(null)
  const [chainItems, setChainItems] = useState<ErrorLogItem[]>([])
  const [chainLoading, setChainLoading] = useState(false)

  const openChain = useCallback(async (incidentId: string) => {
    setChainIncidentId(incidentId)
    setChainItems([])
    setChainLoading(true)
    try {
      const res = await errorLogService.getChain(incidentId) as {
        data?: { items?: ErrorLogItem[] }
      }
      setChainItems(res?.data?.items || [])
    } catch {
      toast.error('加载错误链路失败')
    } finally {
      setChainLoading(false)
    }
  }, [])

  const closeChain = useCallback(() => {
    setChainIncidentId(null)
    setChainItems([])
  }, [])

  return { chainIncidentId, chainItems, chainLoading, openChain, closeChain }
}
