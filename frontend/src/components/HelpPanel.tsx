import React, { useState } from 'react'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ChevronRight, MessageCircle } from 'lucide-react'
import { HELP_SECTIONS, type HelpSectionKey } from './help'
import { BlockRenderer } from './help/HelpBlockViews'

interface HelpPanelProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onOpenFeedback?: () => void
}

const HelpPanel: React.FC<HelpPanelProps> = ({ open, onOpenChange, onOpenFeedback }) => {
  const [expandedSection, setExpandedSection] = useState<HelpSectionKey | null>(null)
  const [expandedStepId, setExpandedStepId] = useState<string | null>(null)

  const toggleSection = (key: HelpSectionKey) => {
    setExpandedSection(prev => (prev === key ? null : key))
    setExpandedStepId(null)
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:w-1/2 sm:max-w-none p-0 flex flex-col">
        <SheetHeader className="px-6 pt-6 pb-4 border-b border-gray-100">
          <SheetTitle className="text-lg font-semibold">帮助中心</SheetTitle>
          <p className="text-sm text-muted-foreground mt-1">
            了解 BidMind AI 的核心功能、操作指南与影响结果质量的关键要点
          </p>
        </SheetHeader>

        <ScrollArea className="flex-1">
          <div className="px-6 py-4 space-y-2">
            {HELP_SECTIONS.map(section => {
              const Icon = section.icon
              const isExpanded = expandedSection === section.key
              return (
                <div key={section.key} className="rounded-lg border border-gray-200 overflow-hidden">
                  <button
                    onClick={() => toggleSection(section.key)}
                    className="w-full flex items-center gap-3 px-4 py-3 hover:bg-gray-50 transition-colors text-left"
                  >
                    <div className={`w-8 h-8 rounded-lg ${section.bgColor} flex items-center justify-center flex-shrink-0`}>
                      <Icon className={`w-4 h-4 ${section.color}`} />
                    </div>
                    <span className="text-sm font-medium text-gray-900 flex-1">{section.title}</span>
                    <ChevronRight
                      className={`w-4 h-4 text-gray-400 transition-transform duration-200 ${isExpanded ? 'rotate-90' : ''}`}
                    />
                  </button>
                  {isExpanded && (
                    <div className="px-4 pb-4 space-y-4">
                      {section.blocks.map((block, index) => (
                        <BlockRenderer
                          key={`${block.kind}-${index}`}
                          block={block}
                          expandedStepId={expandedStepId}
                          onToggleStep={(id) => setExpandedStepId(prev => (prev === id ? null : id))}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          <div className="px-6 py-4 border-t border-gray-100 mt-2 space-y-3">
            {onOpenFeedback && (
              <button
                onClick={() => { onOpenChange(false); onOpenFeedback() }}
                className="w-full flex items-center justify-center gap-2 py-2.5 text-sm font-medium text-gray-600 bg-gray-50 hover:bg-gray-100 rounded-lg transition-colors"
              >
                <MessageCircle className="h-4 w-4" />
                意见反馈
              </button>
            )}
            <p className="text-xs text-gray-400 text-center">BidMind AI 6.8.6</p>
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}

export default HelpPanel
