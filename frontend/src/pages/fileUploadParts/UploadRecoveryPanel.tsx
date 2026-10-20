/**
 * 职责: 投标审查上传页的恢复面板(半成品恢复 / 失败恢复)展示与重置入口
 * 不做: 不判定页面模式、不发起上传/解析、不管理槽位状态
 */
import React from 'react'

export interface RecoverySlotSummary {
  slotId: string
  docName: string
  filename: string | null
}

export interface UploadRecoveryPanelProps {
  mode: 'recover_half_upload' | 'failed_recovery'
  /** 半成品模式下展示各槽位检测结果 */
  slotSummaries: RecoverySlotSummary[]
  /** 失败模式下的失败原因(可空) */
  failureReason?: string | null
  isResetting: boolean
  onReset: () => void
}

const UploadRecoveryPanel: React.FC<UploadRecoveryPanelProps> = ({
  mode,
  slotSummaries,
  failureReason,
  isResetting,
  onReset,
}) => {
  const resetButton = (
    <button
      onClick={onReset}
      disabled={isResetting}
      className={`mt-4 px-4 py-2 rounded-md text-white text-sm ${
        isResetting ? 'bg-gray-400 cursor-not-allowed' : 'bg-red-600 hover:bg-red-700'
      }`}
    >
      {isResetting ? '正在重置...' : '重新开始上传'}
    </button>
  )

  if (mode === 'recover_half_upload') {
    return (
      <div className="p-4 rounded-lg border border-red-200 bg-red-50">
        <h2 className="text-base font-semibold text-red-800 mb-2">上传未完整完成</h2>
        <p className="text-sm text-red-700 leading-relaxed">
          当前项目的文件不完整（缺招标文件或投标文件未齐套），无法继续补传单个文件。
          请重新开始上传，系统会清理本次半成品记录，然后重新同时上传全部文件。
        </p>
        <div className="mt-3 text-sm text-red-700 space-y-1">
          {slotSummaries.map((summary) => (
            <p key={summary.slotId}>
              {summary.docName}: {summary.filename ?? '未检测到'}
            </p>
          ))}
        </div>
        {resetButton}
      </div>
    )
  }

  return (
    <div className="p-4 rounded-lg border border-red-200 bg-red-50">
      <h2 className="text-base font-semibold text-red-800 mb-2">上传或解析失败</h2>
      {failureReason && (
        <p className="text-sm text-red-700 mb-2">原因: {failureReason}</p>
      )}
      <p className="text-sm text-red-700 leading-relaxed">
        当前项目处于失败状态。若上方没有可继续重试的节点，请重新开始上传，
        系统会清理当前上传阶段数据后回到初始上传状态。
      </p>
      {resetButton}
    </div>
  )
}

export default UploadRecoveryPanel
