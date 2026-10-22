import React from 'react'
import { ResponsiveContainer, LineChart, Line, Tooltip, YAxis, XAxis } from 'recharts'

import { formatTrendTooltipValue } from './trendTooltipFormat'

interface Props {
  data: Array<{ ts: string; value: number }>
  color?: string
  height?: number
  showAxis?: boolean
  yLabel?: string
  unit?: string
}

const TrendLine: React.FC<Props> = ({
  data,
  color = '#6366f1',
  height = 60,
  showAxis = false,
  yLabel,
  unit,
}) => {
  if (!data || data.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-xs text-gray-300"
        style={{ height }}
      >
        暂无趋势数据
      </div>
    )
  }
  return (
    <div>
      {(yLabel || unit) && (
        <div className="mb-0.5 text-[10px] text-gray-400">
          {yLabel || '数值'}
          {unit ? `（${unit}）` : ''}
        </div>
      )}
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          {showAxis && (
            <XAxis
              dataKey="ts"
              fontSize={10}
              stroke="#94a3b8"
              tickMargin={4}
              interval="preserveStartEnd"
            />
          )}
          {showAxis && (
            <YAxis
              fontSize={10}
              stroke="#94a3b8"
              width={36}
              allowDecimals={false}
              label={
                yLabel
                  ? {
                      value: yLabel,
                      angle: -90,
                      position: 'insideLeft',
                      style: { fontSize: 10, fill: '#94a3b8' },
                    }
                  : undefined
              }
            />
          )}
          <Tooltip
            contentStyle={{
              fontSize: 12,
              padding: '6px 8px',
              borderRadius: 6,
              border: '1px solid #e5e7eb',
            }}
            labelStyle={{ color: '#6b7280' }}
            formatter={(value) => formatTrendTooltipValue(value, unit, yLabel)}
            labelFormatter={(label) => `时间 ${label}`}
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke={color}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export default TrendLine
