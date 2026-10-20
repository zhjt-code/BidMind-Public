export const FLOW_TABS: { key: string; label: string }[] = [
  { key: 'writing', label: '标书编写' },
  { key: 'bid_review', label: '投标审查' },
  { key: 'collusion', label: '串标审查' },
]

export const CHANNEL_COLORS: Record<string, string> = {
  review: 'bg-blue-100 text-blue-700',
  vision: 'bg-purple-100 text-purple-700',
}

export const NODE_CLASS_LABELS: Record<'mechanical' | 'judgment', string> = {
  mechanical: '机械类',
  judgment: '判定类',
}

export const NODE_CLASS_COLORS: Record<'mechanical' | 'judgment', string> = {
  mechanical: 'bg-green-100 text-green-700',
  judgment: 'bg-amber-100 text-amber-700',
}

export const STATUS_COLORS: Record<string, string> = {
  published: 'bg-green-100 text-green-700',
  draft: 'bg-yellow-100 text-yellow-700',
  validated: 'bg-blue-100 text-blue-700',
  archived: 'bg-gray-100 text-gray-500',
}
