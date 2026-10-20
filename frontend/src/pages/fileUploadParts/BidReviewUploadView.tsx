/**
 * 职责: 投标审查上传页的分区卡片布局
 * 不做: 不发请求、不持有槽位状态、不决定页面模式
 */
import React from 'react'

import SkipVlOption from '@/components/upload/SkipVlOption'
import {
  IMAGE_PACE_LABEL,
  TENDER_IMAGE_PACE_HINT,
} from '@/components/upload/documentPace'

import EnvelopeModeSwitch from './EnvelopeModeSwitch'

export interface BidReviewUploadViewProps {
  error: string | null
  retryPanel: React.ReactNode
  recoveryPanel: React.ReactNode | null
  tenderSlot: React.ReactNode
  bidSlotList: React.ReactNode
  showInputs: boolean
  skipVlChecked: boolean
  skipVlDisabled: boolean
  onSkipVlChange: (checked: boolean) => void
  envelopeMode: string
  envelopeDisabled: boolean
  onEnvelopeChange: (mode: string) => void
  actions: React.ReactNode
  postProcessHint: string
}

const BidReviewUploadView: React.FC<BidReviewUploadViewProps> = ({
  error,
  retryPanel,
  recoveryPanel,
  tenderSlot,
  bidSlotList,
  showInputs,
  skipVlChecked,
  skipVlDisabled,
  onSkipVlChange,
  envelopeMode,
  envelopeDisabled,
  onEnvelopeChange,
  actions,
  postProcessHint,
}) => (
  <div className="mx-auto max-w-2xl">
    {error && (
      <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-4">
        <p className="text-sm text-red-800">{error}</p>
      </div>
    )}
    {retryPanel}
    {recoveryPanel ? (
      <div className="bg-white rounded-lg shadow p-6 space-y-6">
        {recoveryPanel}
        {actions}
      </div>
    ) : (
      <div className="space-y-6">
        <div className="bg-white rounded-lg shadow p-6">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
            <h2 className="text-base font-semibold text-gray-800">招标文件 (PDF/Word)</h2>
            {showInputs ? (
              <SkipVlOption
                id="skip-vl-tender"
                checked={skipVlChecked}
                disabled={skipVlDisabled}
                onChange={onSkipVlChange}
                label={IMAGE_PACE_LABEL}
                description={TENDER_IMAGE_PACE_HINT}
              />
            ) : null}
          </div>
          {tenderSlot}
        </div>
        <div className="bg-white rounded-lg shadow p-6">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
            <h2 className="text-base font-semibold text-gray-800">投标文件 (PDF/Word)</h2>
            {showInputs ? (
              <EnvelopeModeSwitch
                mode={envelopeMode}
                disabled={envelopeDisabled}
                onChange={onEnvelopeChange}
                disabledHint="已有投标文件时不可切换构成"
              />
            ) : null}
          </div>
          <div className="space-y-4">{bidSlotList}</div>
        </div>
        {actions}
      </div>
    )}
    <p className="mt-3 text-xs text-muted-foreground">
      {postProcessHint}
    </p>
  </div>
)

export default BidReviewUploadView
