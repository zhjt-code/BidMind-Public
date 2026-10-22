/**
 * 职责: 文档图片识别节奏控件(加速 / 完整识别)并映射到 skip_vl
 * 不做: 不持久化偏好、不发起解析、不判断扫描件
 */
import React from 'react'

import FieldHintButton from '@/components/upload/FieldHintButton'
import {
  DOCUMENT_PACE_OPTIONS,
  documentPaceFromSkipVl,
  skipVlFromDocumentPace,
  type DocumentPace,
} from '@/components/upload/documentPace'
import SegmentedControl from '@/components/upload/SegmentedControl'

export interface SkipVlOptionProps {
  id: string
  checked: boolean
  label: string
  description: string
  disabled?: boolean
  onChange: (checked: boolean) => void
}

const SkipVlOption: React.FC<SkipVlOptionProps> = ({
  id,
  checked,
  label,
  description,
  disabled = false,
  onChange,
}) => {
  const pace = documentPaceFromSkipVl(checked)
  return (
    <div className="flex flex-col items-stretch gap-1 sm:items-end">
      <div className="flex items-center gap-2">
        <p id={`${id}-label`} className="shrink-0 text-sm font-medium text-[#1c2733]">
          {label}
        </p>
        <SegmentedControl
          ariaLabel={label}
          value={pace}
          options={DOCUMENT_PACE_OPTIONS}
          disabled={disabled}
          onChange={(next) => onChange(skipVlFromDocumentPace(next as DocumentPace))}
        />
        <FieldHintButton label={description}>{description}</FieldHintButton>
      </div>
    </div>
  )
}

export default SkipVlOption
