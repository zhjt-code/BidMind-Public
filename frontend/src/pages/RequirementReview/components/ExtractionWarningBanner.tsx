/**
 * 提取完成但部分内容需要人工核对的警告条。
 *
 * displays 由调用方通过 formatExtractionWarning 派生 (含 summary + details)。
 * displays 为空数组时本组件不渲染。
 */
import React from 'react'
import {
  extractionWarningBannerTitle,
  type ExtractionWarningDisplay,
} from '../extractionWarningCopy'

export interface ExtractionWarningBannerProps {
  displays: ExtractionWarningDisplay[]
}

const TONE_LABEL: Record<string, string> = {
  action: '请核对',
  info: '已自动处理',
}

export const ExtractionWarningBanner: React.FC<ExtractionWarningBannerProps> = ({
  displays,
}) => {
  if (displays.length === 0) return null
  return (
    <div className="mb-4 p-4 bg-amber-50 rounded-lg border border-amber-300 flex items-start">
      <svg
        className="w-5 h-5 text-amber-500 mt-0.5 mr-2 flex-shrink-0"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
        />
      </svg>
      <div className="space-y-2">
        <p className="text-sm font-medium text-amber-900">
          {extractionWarningBannerTitle(displays)}
        </p>
        {displays.map((warning) => (
          <div key={warning.raw} className="text-sm text-amber-800">
            {warning.kind === 'mandatory' && (
              <p className="text-xs font-medium text-amber-900">
                {TONE_LABEL[warning.tone]}
              </p>
            )}
            <p>{warning.summary}</p>
            {warning.details.length > 0 && (
              <ul className="mt-1 list-disc space-y-1 pl-5">
                {warning.details.map((detail) => (
                  <li key={detail}>{detail}</li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
