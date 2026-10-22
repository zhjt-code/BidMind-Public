import React, { useEffect, useRef, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { GateWarningSummary } from '../writingComposeGateFlags'

interface WritingGateReviewSummaryProps {
  summary: GateWarningSummary
  onSelectNode: (nodeId: number) => void
}

export const WritingGateReviewSummary: React.FC<WritingGateReviewSummaryProps> = ({
  summary,
  onSelectNode,
}) => {
  const [open, setOpen] = useState(false)
  const popoverRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return undefined
    const handlePointer = (event: MouseEvent) => {
      if (popoverRef.current?.contains(event.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', handlePointer, true)
    return () => {
      document.removeEventListener('mousedown', handlePointer, true)
    }
  }, [open])

  if (summary.total === 0) return null

  const high = summary.high > 0
  return (
    <div ref={popoverRef} className="relative">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className={`h-7 gap-1.5 text-xs ${high ? 'border-red-200 bg-red-50 text-red-700 hover:bg-red-100' : 'border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100'}`}
        onClick={() => setOpen((value) => !value)}
      >
        <AlertTriangle className="h-3.5 w-3.5" />
        需复核 {summary.total}
        {high && <Badge className="h-4 px-1 text-[10px] bg-red-600">{summary.high}</Badge>}
      </Button>
      {open && (
        <div className="absolute left-0 top-9 z-30 w-80 rounded-md border bg-white shadow-lg">
          <div className="border-b px-3 py-2 text-xs font-medium text-muted-foreground">
            需人工关注的章节
          </div>
          <div className="max-h-72 overflow-auto py-1">
            {summary.items.map((item) => {
              const firstBadge = item.badges[0]
              const itemHigh = item.badges.some((badge) => badge.severity === 'high')
              return (
                <button
                  key={item.nodeId}
                  type="button"
                  className="block w-full px-3 py-2 text-left hover:bg-muted/50"
                  onClick={() => {
                    onSelectNode(item.nodeId)
                    setOpen(false)
                  }}
                >
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm">{item.title}</span>
                    <span className={`rounded px-1.5 py-0.5 text-[10px] ${itemHigh ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'}`}>
                      {firstBadge.label}
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                    {firstBadge.hint}
                  </p>
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
