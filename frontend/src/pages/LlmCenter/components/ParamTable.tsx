import React from 'react'
import type { LlmParamDef } from '@/services/llmCenterApi'

interface ParamTableProps {
  params: LlmParamDef[]
  direction: string
}

export const ParamTable: React.FC<ParamTableProps> = ({ params, direction }) => {
  const filtered = params.filter(p => p.direction === direction && p.status !== 'removed')
  if (filtered.length === 0) return <p className="text-xs text-gray-400 italic">无{direction}参数</p>

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b text-gray-500">
            <th className="text-left py-1.5 font-medium">参数</th>
            <th className="text-left py-1.5 font-medium">类型</th>
            <th className="text-left py-1.5 font-medium">必填</th>
            <th className="text-left py-1.5 font-medium">来源</th>
            <th className="text-left py-1.5 font-medium">说明</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map(p => (
            <tr key={p.id} className={`border-b border-gray-100 ${p.status === 'deprecated' ? 'opacity-50' : ''}`}>
              <td className="py-1.5 font-mono text-gray-800">{p.param_key}</td>
              <td className="py-1.5 text-gray-500">{p.data_type}</td>
              <td className="py-1.5">{p.is_required ? <span className="text-red-500">*</span> : '-'}</td>
              <td className="py-1.5 text-gray-500">{p.source_type}</td>
              <td className="py-1.5 text-gray-500 max-w-[200px] truncate">{p.description || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
