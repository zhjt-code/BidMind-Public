import React from 'react'
import type { MaterialChecklistItem } from '@/services/writingApi'
import { formatChecklistSubRequirement } from '../materialChecklistDisplay.ts'

interface ChecklistItemProps {
  index: number
  item: MaterialChecklistItem
  primaryColor: string
  secondaryColor: string
}

export const ChecklistItem: React.FC<ChecklistItemProps> = ({
  index,
  item,
  primaryColor,
  secondaryColor,
}) => {
  const hasSubItems = item.subRequirements && item.subRequirements.length > 0

  return (
    <div>
      <p className={`text-sm font-medium ${primaryColor}`}>
        {index + 1}. {item.requirement.trim()}
      </p>
      {hasSubItems && (
        <div className="ml-5 mt-1.5 space-y-1">
          {item.subRequirements.map((sub, subIdx) => (
            <p key={subIdx} className={`text-sm ${secondaryColor}`}>
              ({subIdx + 1}) {formatChecklistSubRequirement(sub)}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
