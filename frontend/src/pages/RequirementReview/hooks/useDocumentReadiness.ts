/**
 * 招标 / 投标文档就绪状态查询 hook。
 *
 * 从原 RequirementReview.tsx::loadRequirements 内嵌段抽出 (L141-187):
 *   - 单次拉取 documentService.getDocuments
 *   - 派生 tender / bid 双侧就绪状态 + 文案
 *   - 派生 tenderDocId (用于 PDF 预览兜底与 requirement 行 fallback)
 *
 * 关键契约 (零行为变化):
 *   - 三态枚举: ready=true(已就绪) / ready=false(缺失或解析中) / ready=null(检查中)
 *   - 缺失文档与"已上传但 parsed !== 1"的提示文案严格分离
 *   - 拉取异常不抛: 静默忽略,由后端防御性检查兜底
 *
 * 返回的 refresh() 同时返回**同步快照值**,供调用方做"避免 setState 异步漂移"的
 * 同步决策 (例如"文档未就绪则跳过自动提取触发"分支)。
 */

import { useCallback, useState } from 'react'
import { documentService } from '@/services/documentApi'
import { deriveBidReadiness } from '@/lib/bidEnvelope'

export interface DocumentReadinessSnapshot {
  tenderDocReady: boolean | null
  tenderDocMessage: string
  tenderDocId: number | null
  bidDocReady: boolean | null
  bidDocMessage: string
}

export interface UseDocumentReadinessOptions {
  projectId: string | undefined
}

export interface UseDocumentReadinessReturn extends DocumentReadinessSnapshot {
  /**
   * 检查并同步更新文档就绪状态。
   * 返回快照值,供调用方在同步控制流中直接判定 (避免依赖 setState 异步).
   */
  refresh: () => Promise<DocumentReadinessSnapshot>
}

export function useDocumentReadiness(
  options: UseDocumentReadinessOptions,
): UseDocumentReadinessReturn {
  const { projectId } = options

  const [tenderDocReady, setTenderDocReady] = useState<boolean | null>(null)
  const [tenderDocMessage, setTenderDocMessage] = useState('')
  const [tenderDocId, setTenderDocId] = useState<number | null>(null)
  const [bidDocReady, setBidDocReady] = useState<boolean | null>(null)
  const [bidDocMessage, setBidDocMessage] = useState('')

  const refresh = useCallback(async (): Promise<DocumentReadinessSnapshot> => {
    let nextTenderReady: boolean | null = null
    let nextTenderMessage = ''
    let nextTenderId: number | null = null
    let nextBidReady: boolean | null = null
    let nextBidMessage = ''

    if (!projectId) {
      return {
        tenderDocReady: null,
        tenderDocMessage: '',
        tenderDocId: null,
        bidDocReady: null,
        bidDocMessage: '',
      }
    }

    try {
      const docs = await documentService.getDocuments(parseInt(projectId))
      const docData = (docs as {
        data?: {
          tender?: { id?: number; parsed: number }
          bid?: { parsed: number; bid_part?: string | null }
          bids?: Array<{ parsed: number; bid_part?: string | null }>
        }
      }).data

      if (!docData?.tender) {
        nextTenderReady = false
        nextTenderMessage = '项目缺少招标文档，请先返回上传页面上传招标文件'
        nextTenderId = null
      } else if (docData.tender.parsed !== 1) {
        nextTenderReady = false
        nextTenderMessage = '招标文件尚未处理完成，请等待处理完成后再操作'
        nextTenderId = typeof docData.tender.id === 'number' ? docData.tender.id : null
      } else {
        nextTenderReady = true
        nextTenderMessage = ''
        nextTenderId = typeof docData.tender.id === 'number' ? docData.tender.id : null
      }

      // 投标侧按信封判定(整本 1 份 或 分卷各 1 份), 文案点名缺失/处理中的卷
      const bids = docData?.bids ?? (docData?.bid ? [docData.bid] : [])
      const bidReadiness = deriveBidReadiness(bids)
      nextBidReady = bidReadiness.ready
      nextBidMessage = bidReadiness.message

      setTenderDocReady(nextTenderReady)
      setTenderDocMessage(nextTenderMessage)
      setTenderDocId(nextTenderId)
      setBidDocReady(nextBidReady)
      setBidDocMessage(nextBidMessage)
    } catch {
      // 检查失败不阻塞，后端 extract_requirements 有防御性检查
    }

    return {
      tenderDocReady: nextTenderReady,
      tenderDocMessage: nextTenderMessage,
      tenderDocId: nextTenderId,
      bidDocReady: nextBidReady,
      bidDocMessage: nextBidMessage,
    }
  }, [projectId])

  return {
    tenderDocReady,
    tenderDocMessage,
    tenderDocId,
    bidDocReady,
    bidDocMessage,
    refresh,
  }
}
