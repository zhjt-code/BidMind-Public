import React, { useState } from 'react'
import type { GlobalFindingItem } from '@/services/collusionApi'
import { Users, Building, DollarSign, FileText, ChevronDown, ChevronRight } from 'lucide-react'
import RichTextContent from '@/components/RichTextContent'

const FINDING_TYPE_CONFIG: Record<string, { icon: React.ElementType; label: string; bg: string }> = {
  personnel_overlap: { icon: Users, label: '\u4eba\u5458\u91cd\u53e0', bg: 'bg-red-50 border-red-200 text-red-700' },
  company_relation: { icon: Building, label: '\u4f01\u4e1a\u5173\u8054', bg: 'bg-orange-50 border-orange-200 text-orange-700' },
  pricing_pattern: { icon: DollarSign, label: '\u62a5\u4ef7\u5f02\u5e38', bg: 'bg-purple-50 border-purple-200 text-purple-700' },
  metadata_cluster: { icon: FileText, label: '\u5143\u6570\u636e\u805a\u7c7b', bg: 'bg-blue-50 border-blue-200 text-blue-700' },
  cross_field_correlation: { icon: FileText, label: '\u8de8\u5b57\u6bb5\u4e32\u8054', bg: 'bg-violet-50 border-violet-200 text-violet-700' },
}

interface GlobalFindingsProps {
  findings: GlobalFindingItem[]
}

const GlobalFindings: React.FC<GlobalFindingsProps> = ({ findings }) => {
  const [expanded, setExpanded] = useState(true)

  if (!findings || findings.length === 0) return null

  const grouped: Record<string, GlobalFindingItem[]> = {}
  for (const f of findings) {
    const type = f.type || 'unknown'
    if (!grouped[type]) grouped[type] = []
    grouped[type].push(f)
  }

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 w-full text-left"
      >
        {expanded
          ? <ChevronDown className="w-3 h-3 text-gray-400" />
          : <ChevronRight className="w-3 h-3 text-gray-400" />
        }
        <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">
          {'\u5168\u5c40\u53d1\u73b0'} ({findings.length})
        </span>
      </button>

      {expanded && (
        <div className="mt-1.5 space-y-1.5">
          {Object.entries(grouped).map(([type, items]) => {
            // A3: 未知 type 回退使用通用中文文案，杜绝裸出英文 type
            const config = FINDING_TYPE_CONFIG[type] || {
              icon: FileText,
              label: '\u5176\u4ed6\u5168\u5c40\u53d1\u73b0',
              bg: 'bg-gray-50 border-gray-200 text-gray-600',
            }
            const Icon = config.icon
            return (
              <div key={type} className={`rounded border p-2 ${config.bg}`}>
                <div className="flex items-center gap-1 mb-1">
                  <Icon className="w-3 h-3" />
                  <span className="text-[10px] font-semibold">{config.label} ({items.length})</span>
                </div>
                {items.map((item, i) => (
                  <div key={`${type}-${i}`} className="leading-relaxed text-gray-700">
                    <RichTextContent text={item.description} variant="compact" />
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default GlobalFindings
