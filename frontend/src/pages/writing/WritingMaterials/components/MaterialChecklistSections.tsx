import React from 'react'
import { AlertTriangle, ChevronDown, Info } from 'lucide-react'
import type { MaterialChecklistItem } from '@/services/writingApi'
import { ChecklistItem } from './ChecklistItem.tsx'

interface MaterialChecklistSectionsProps {
  missingMaterials: MaterialChecklistItem[]
  notices: MaterialChecklistItem[]
  showMaterials: boolean
  showNotices: boolean
  onToggleMaterials: () => void
  onToggleNotices: () => void
}

export const MaterialChecklistSections: React.FC<MaterialChecklistSectionsProps> = ({
  missingMaterials,
  notices,
  showMaterials,
  showNotices,
  onToggleMaterials,
  onToggleNotices,
}) => {
  const missingCount = missingMaterials.length

  return (
    <>
      {missingCount > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-4">
          <button
            onClick={onToggleMaterials}
            className="flex items-center gap-2 w-full text-left"
          >
            <ChevronDown
              className={`h-4 w-4 text-amber-600 transition-transform ${showMaterials ? '' : '-rotate-90'}`}
            />
            <AlertTriangle className="h-4 w-4 text-amber-600" />
            <h3 className="font-medium text-amber-800">需上传素材 ({missingCount} 项)</h3>
          </button>
          {showMaterials && (
            <div className="mt-3 ml-6 space-y-2">
              {missingMaterials.map((item, idx) => (
                <ChecklistItem
                  key={item.clauseId}
                  index={idx}
                  item={item}
                  primaryColor="text-amber-800"
                  secondaryColor="text-amber-700"
                />
              ))}
            </div>
          )}
        </div>
      )}

      {notices.length > 0 && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-4">
          <button
            onClick={onToggleNotices}
            className="flex items-center gap-2 w-full text-left"
          >
            <ChevronDown
              className={`h-4 w-4 text-blue-600 transition-transform ${showNotices ? '' : '-rotate-90'}`}
            />
            <Info className="h-4 w-4 text-blue-600" />
            <h3 className="font-medium text-blue-800">注意事项 ({notices.length} 项)</h3>
          </button>
          {showNotices && (
            <div className="mt-3 ml-6 space-y-2">
              {notices.map((item, idx) => (
                <ChecklistItem
                  key={item.clauseId}
                  index={idx}
                  item={item}
                  primaryColor="text-blue-800"
                  secondaryColor="text-blue-700"
                />
              ))}
            </div>
          )}
        </div>
      )}
    </>
  )
}
