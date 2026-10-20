import React, { useState } from 'react'
import type { CollusionGroupResult, CollusionPairResult } from '@/services/collusionApi'
import { ChevronDown, ChevronRight, Shield, Search, Users } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import RichTextContent from '@/components/RichTextContent'

const ROLE_COLORS: Record<string, string> = {
  '主标方': 'bg-red-100 text-red-700 border-red-200',
  '陪标方': 'bg-orange-100 text-orange-700 border-orange-200',
  '组织者': 'bg-purple-100 text-purple-700 border-purple-200',
  '角色不明': 'bg-gray-100 text-gray-600 border-gray-200',
  '独立方': 'bg-blue-100 text-blue-700 border-blue-200',
}

const RISK_LABEL: Record<string, { label: string; color: string }> = {
  high: { label: '高风险', color: 'text-red-600' },
  medium: { label: '中风险', color: 'text-orange-600' },
  low: { label: '低风险', color: 'text-yellow-600' },
}

interface GroupDetailProps {
  group: CollusionGroupResult | null
  pairResults: CollusionPairResult[]
  replaceRefs: (text: string | undefined) => string
}

const GroupDetail: React.FC<GroupDetailProps> = ({ group, replaceRefs }) => {
  const [showDetails, setShowDetails] = useState(false)

  if (!group) return null

  const risk = RISK_LABEL[group.riskLevel] || RISK_LABEL.medium

  return (
    <div className="px-3 py-2.5 space-y-2">
      {/* 风险 + 成员 */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`text-xs font-bold ${risk.color}`}>{risk.label}</span>
        <span className="text-xs text-gray-400">|</span>
        {group.groupConfidence !== undefined && (
          <>
            <span className="text-xs text-gray-500">可信度 {Math.round(group.groupConfidence * 100)}%</span>
            <span className="text-xs text-gray-400">|</span>
          </>
        )}
        {group.uncertainEdgeCount > 0 && (
          <>
            <span className="text-xs text-amber-600">待复核边 {group.uncertainEdgeCount}</span>
            <span className="text-xs text-gray-400">|</span>
          </>
        )}
        {group.memberNames.map((name, i) => (
          <span key={`m-${group.groupIndex}-${i}`} className="text-xs px-1.5 py-0.5 bg-gray-100 text-gray-700 rounded truncate max-w-[120px]">
            {name}
          </span>
        ))}
      </div>

      {/* 串通策略 */}
      {group.collusionStrategy && (
        <div className="flex items-start gap-1.5">
          <Shield className="w-3 h-3 text-orange-500 mt-0.5 shrink-0" />
          <RichTextContent text={replaceRefs(group.collusionStrategy)} className="text-xs text-gray-600 leading-relaxed" variant="compact" />
        </div>
      )}

      {/* 展开/折叠 更多详情 */}
      <button
        onClick={() => setShowDetails(!showDetails)}
        className="flex items-center gap-1 text-[11px] text-gray-400 hover:text-gray-600 transition-colors"
      >
        {showDetails ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        {showDetails ? '收起详情' : '角色分析·调查建议'}
      </button>

      {showDetails && (
        <div className="space-y-2 pt-1">
          {/* 角色分析 */}
          {group.roles && group.roles.length > 0 && (
            <div className="space-y-1">
              <div className="flex items-center gap-1">
                <Users className="w-3 h-3 text-gray-400" />
                <span className="text-[11px] font-semibold text-gray-500">成员角色</span>
              </div>
              {group.roles.map((role, i) => {
                const roleColor = ROLE_COLORS[role.role] || ROLE_COLORS['角色不明']
                return (
                  <div key={`r-${group.groupIndex}-${role.docId}-${i}`} className="flex items-center gap-2 pl-4">
                    <Badge className={`text-[10px] border px-1.5 py-0 ${roleColor}`}>
                      {role.role}
                    </Badge>
                    <span className="text-[11px] text-gray-700 truncate">
                      {role.companyName || `文件${role.docId}`}
                    </span>
                  </div>
                )
              })}
            </div>
          )}

          {/* 调查建议 */}
          {group.investigationFocus && group.investigationFocus.length > 0 && (
            <div className="space-y-1">
              <div className="flex items-center gap-1">
                <Search className="w-3 h-3 text-gray-400" />
                <span className="text-[11px] font-semibold text-gray-500">调查建议</span>
              </div>
              <ol className="list-decimal pl-7 space-y-0.5">
                {group.investigationFocus.map((item, i) => (
                  <li key={`f-${group.groupIndex}-${i}`} className="text-[11px] text-gray-600">
                    <RichTextContent text={replaceRefs(item)} className="inline" variant="compact" />
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default GroupDetail
