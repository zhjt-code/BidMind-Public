import React from 'react'

export const DASHBOARD_WINDOW_DAYS = [7, 30, 90] as const

interface WindowDaysToggleProps {
  value: number
  onChange: (days: number) => void
}

const WindowDaysToggle: React.FC<WindowDaysToggleProps> = ({ value, onChange }) => (
  <div className="flex shrink-0 rounded-lg border border-slate-200 p-0.5">
    {DASHBOARD_WINDOW_DAYS.map((days) => (
      <button
        key={days}
        type="button"
        onClick={() => onChange(days)}
        className={`rounded-md px-2 py-1 text-[11px] ${
          value === days ? 'bg-cyan-50 text-cyan-800' : 'text-slate-500 hover:text-slate-800'
        }`}
      >
        {days}天
      </button>
    ))}
  </div>
)

export default WindowDaysToggle
