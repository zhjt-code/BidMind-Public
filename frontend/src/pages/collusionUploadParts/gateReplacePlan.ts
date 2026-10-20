// 门禁"返回更换文档"处置计划(纯函数, 供 CollusionUpload 消费)

// 约束: 用户选择"返回更换文档"即表示阻断全过程——本批全部文档
// (可疑+干净)必须一并撤销, 禁止对任何文档发起解析。干净文件回到待上传态,
// 待用户补齐后重新点击"上传并处理", 让 MIN_BID 门禁与招标白名单意图重新生效。

export interface GateReplaceEntry {
  key: string
  type: 'tender' | 'bid'
  documentId: number
}

export interface GateReplacePlan {
  /** 需从服务端删除的文档ID(本批全部, 可疑+干净) */
  deleteDocumentIds: number[]
  /** 可疑投标文件: 从列表移除待更换 */
  removeBidKeys: string[]
  /** 招标文件可疑: 清空待重新选择 */
  clearTender: boolean
  /** 招标文件干净: 回到待上传状态(下轮重传) */
  resetTenderToPending: boolean
  /** 干净投标文件: 回到待上传状态(下轮重传) */
  resetBidKeysToPending: string[]
}

export function buildGateReplacePlan(
  flagged: GateReplaceEntry[],
  clean: GateReplaceEntry[],
): GateReplacePlan {
  return {
    deleteDocumentIds: [...flagged, ...clean].map((entry) => entry.documentId),
    removeBidKeys: flagged
      .filter((entry) => entry.type === 'bid')
      .map((entry) => entry.key),
    clearTender: flagged.some((entry) => entry.type === 'tender'),
    resetTenderToPending: clean.some((entry) => entry.type === 'tender'),
    resetBidKeysToPending: clean
      .filter((entry) => entry.type === 'bid')
      .map((entry) => entry.key),
  }
}
