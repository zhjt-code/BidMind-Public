/**
 * 职责: 互斥分段选择(radiogroup)
 * 不做: 不解释选项含义、不持久化、不绑定具体业务字段
 */
import React from 'react'

import { cn } from '@/lib/utils'

export interface SegmentedControlOption {
  value: string
  label: string
  accessory?: React.ReactNode
  ariaLabel?: string
}

export interface SegmentedControlProps {
  value: string
  options: readonly SegmentedControlOption[]
  onChange: (value: string) => void
  disabled?: boolean
  ariaLabel: string
  emphasized?: boolean
}

const SegmentedControl: React.FC<SegmentedControlProps> = ({
  value,
  options,
  onChange,
  disabled = false,
  ariaLabel,
  emphasized = false,
}) => (
  <div
    role="radiogroup"
    aria-label={ariaLabel}
    className="inline-flex max-w-full flex-wrap rounded-md border border-[rgba(28,39,51,0.14)] bg-[rgba(28,39,51,0.045)] p-0.5"
  >
    {options.map((option) => {
      const selected = option.value === value
      return (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={selected}
          disabled={disabled && !selected}
          onClick={() => {
            if (option.value !== value) onChange(option.value)
          }}
          aria-label={option.ariaLabel}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-[5px] px-3 py-1.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
            selected
              ? emphasized
                ? 'bg-blue-500 text-white shadow-sm'
                : 'bg-white text-[#1c2733] shadow-sm'
              : disabled
                ? 'cursor-not-allowed text-gray-400'
                : 'text-gray-600 hover:text-[#1c2733]',
          )}
        >
          {option.label}
          {option.accessory}
        </button>
      )
    })}
  </div>
)

export default SegmentedControl
