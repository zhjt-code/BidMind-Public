import React from 'react'
import type { LlmTemplateVersion } from '@/services/llmCenterApi'
import { STATUS_COLORS } from '../lib/constants'

interface PromptPreviewProps {
  version: LlmTemplateVersion
}

export const PromptPreview: React.FC<PromptPreviewProps> = ({ version }) => (
  <div className="mb-3">
    <div className="flex items-center gap-2 mb-1">
      <span className="text-xs font-medium text-gray-600 uppercase">{version.template_role}</span>
      <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${STATUS_COLORS[version.status] || 'bg-gray-100'}`}>
        {version.status}
      </span>
      <span className="text-[10px] text-gray-400">v{version.version} ({version.source})</span>
    </div>
    <pre className="bg-gray-900 text-gray-100 rounded-lg p-3 text-xs overflow-auto max-h-64 whitespace-pre-wrap leading-relaxed">
      {version.content}
    </pre>
  </div>
)
