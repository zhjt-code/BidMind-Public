import { getSubRequirementLabel } from '../../../lib/constants.ts'
import { sanitizeUserFacingText } from '../../../services/progressStatusTransforms.ts'

export function formatChecklistSubRequirement(subRequirement: string): string {
  const mapped = getSubRequirementLabel(subRequirement)
  return sanitizeUserFacingText(mapped) || '待补充要求'
}
