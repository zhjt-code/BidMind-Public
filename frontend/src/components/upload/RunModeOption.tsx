/**
 * 职责: 上传页人工确认 / 一步到位分段选择与风险说明
 * 不做: 不发起 PATCH、不决定路由、不绑定项目状态机
 */
import React from 'react'

import FieldHintButton from '@/components/upload/FieldHintButton'
import SegmentedControl from '@/components/upload/SegmentedControl'
import {
  EXPRESS_QUALITY_WARNING,
  getExpressRiskLines,
  getRunModeFieldHint,
  RUN_MODE_BASE_HINT,
  RUN_MODE_LABEL,
  RUN_MODE_OPTIONS,
} from '@/lib/runModeCopy'
import type { RunMode } from '@/lib/runMode'

export interface RunModeOptionProps {
  value: RunMode
  disabled?: boolean
  projectType?: string
  onChange: (value: RunMode) => void
}

const RunModeOption: React.FC<RunModeOptionProps> = ({
  value,
  disabled = false,
  projectType,
  onChange,
}) => {
  const risks = value === 'express' ? getExpressRiskLines(projectType) : []
  const hint = getRunModeFieldHint(value, projectType)

  return (
    <div className="relative flex flex-col items-stretch gap-1 sm:items-end">
      <div className="flex items-center gap-2">
        <p className="shrink-0 text-sm font-medium text-[#1c2733]">{RUN_MODE_LABEL}</p>
        <SegmentedControl
          ariaLabel={RUN_MODE_LABEL}
          value={value}
          options={RUN_MODE_OPTIONS}
          disabled={disabled}
          emphasized
          onChange={(next) => onChange(next as RunMode)}
        />
        <FieldHintButton label={hint}>
          <p className="text-sm font-semibold leading-snug text-amber-200">{EXPRESS_QUALITY_WARNING}</p>
          <p className="mt-1">{RUN_MODE_BASE_HINT}</p>
          {risks.length > 0 ? (
            <ul className="mt-1 list-disc space-y-0.5 pl-4">
              {risks.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : null}
        </FieldHintButton>
      </div>
    </div>
  )
}

export default RunModeOption
