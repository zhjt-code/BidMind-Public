/**
 * 职责: 按信封卷角色将来源页码分组，供结果详情分行展示
 * 不做: 不渲染 UI、不跳转预览、不解析 sourcePages 字符串
 */
import {
  BID_ENVELOPE_PROFILES,
  bidPartLabel,
  type BidEnvelopeProfile,
} from '../../lib/bidEnvelope.ts'

export interface SourcePageLocationItem {
  documentId: number
  part: string | null
  page: number
}

export interface SourcePagePartGroup {
  partKey: string | null
  label: string
  locations: SourcePageLocationItem[]
}

function namedPartOrder(profiles: BidEnvelopeProfile[]): string[] {
  const seen = new Set<string>()
  const order: string[] = []
  for (const profile of profiles) {
    for (const slot of profile.slots) {
      if (!slot.bidPart || seen.has(slot.bidPart)) continue
      seen.add(slot.bidPart)
      order.push(slot.bidPart)
    }
  }
  return order
}

function locationKey(part: string | null): string {
  return part ?? ''
}

export function groupSourcePageLocations(
  locations: readonly SourcePageLocationItem[],
  profiles: BidEnvelopeProfile[] = BID_ENVELOPE_PROFILES,
  labelOf: (part: string | null | undefined) => string = bidPartLabel,
): SourcePagePartGroup[] {
  const buckets = new Map<string, SourcePagePartGroup>()
  const firstSeen: string[] = []

  for (const location of locations) {
    const key = locationKey(location.part)
    let group = buckets.get(key)
    if (!group) {
      group = {
        partKey: location.part,
        label: labelOf(location.part),
        locations: [],
      }
      buckets.set(key, group)
      firstSeen.push(key)
    }
    group.locations.push(location)
  }

  const namedKnown = namedPartOrder(profiles)
  const namedKnownSet = new Set(namedKnown)
  const orderedKeys = [
    ...namedKnown.filter((key) => buckets.has(key)),
    ...firstSeen.filter((key) => key !== '' && !namedKnownSet.has(key)),
    ...(buckets.has('') ? [''] : []),
  ]

  return orderedKeys
    .map((key) => buckets.get(key))
    .filter((group): group is SourcePagePartGroup => group != null)
}
