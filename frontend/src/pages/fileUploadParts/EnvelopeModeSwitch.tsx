/**
 * 职责: 投标信封模式切换控件(整本 / 商务标+技术标)
 * 不做: 不判定切换是否允许(disabled 由页面传入)、不清理槽位状态
 */
import React from 'react'

import FieldHintButton from '@/components/upload/FieldHintButton'
import SegmentedControl from '@/components/upload/SegmentedControl'
import { BID_ENVELOPE_PROFILES } from '@/lib/bidEnvelope'

export interface EnvelopeModeSwitchProps {
  mode: string
  disabled: boolean
  onChange: (mode: string) => void
  disabledHint?: string
}

const ENVELOPE_MODE_HINT =
  '分册投标请分别上传商务标与技术标；系统将一并解析，审查时仍按同一份投标文件提取，不新增分类。'

const EnvelopeModeSwitch: React.FC<EnvelopeModeSwitchProps> = ({
  mode,
  disabled,
  onChange,
  disabledHint,
}) => (
  <div className="flex flex-col items-stretch gap-1 sm:items-end">
    <div className="flex items-center gap-2">
      <SegmentedControl
        ariaLabel="投标文件构成"
        value={mode}
        options={BID_ENVELOPE_PROFILES.map((profile) => ({
          value: profile.mode,
          label: profile.label,
        }))}
        disabled={disabled}
        onChange={onChange}
      />
      <FieldHintButton label={ENVELOPE_MODE_HINT}>{ENVELOPE_MODE_HINT}</FieldHintButton>
    </div>
    {disabled && disabledHint ? (
      <p className="text-[11px] text-muted-foreground">{disabledHint}</p>
    ) : null}
  </div>
)

export default EnvelopeModeSwitch
