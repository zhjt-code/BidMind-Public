import React, { useState, useRef, useEffect } from 'react'
import ServiceWechatContact from '@/components/ServiceWechatContact'
import {
  dismissFeedbackFab,
  isFeedbackFabDismissed,
} from '@/lib/feedbackFabVisibility'
import { feedbackService } from '@/services/feedbackApi'

interface FeedbackPanelProps {
  defaultType?: 'bug' | 'feature' | 'review_question' | 'other'
  projectId?: number
  visible?: boolean
  onClose?: () => void
}

const FEEDBACK_TYPES = [
  { key: 'feature', label: '功能建议' },
  { key: 'bug', label: 'Bug报告' },
  { key: 'review_question', label: '审查疑问' },
  { key: 'other', label: '其他' },
] as const

const PLACEHOLDER_MAP: Record<string, string> = {
  feature: '请描述您希望增加或改进的功能...',
  bug: '请描述问题的具体表现和复现步骤...',
  review_question: '请描述您对审查结果的疑问...',
  other: '请描述您的问题或建议...',
}

const FeedbackPanel: React.FC<FeedbackPanelProps> = ({
  defaultType,
  projectId,
  visible: controlledVisible,
  onClose,
}) => {
  const [isOpen, setIsOpen] = useState(false)
  const [feedbackType, setFeedbackType] = useState<string>(defaultType || 'feature')
  const [content, setContent] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [showSuccess, setShowSuccess] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState(() => isFeedbackFabDismissed())
  const panelRef = useRef<HTMLDivElement>(null)

  const isControlled = controlledVisible !== undefined
  const panelVisible = isControlled ? controlledVisible : isOpen

  useEffect(() => {
    if (defaultType) setFeedbackType(defaultType)
  }, [defaultType])

  const handleOpen = () => {
    if (!isControlled) setIsOpen(true)
    setError(null)
    setShowSuccess(false)
  }

  const handleClose = () => {
    if (!isControlled) setIsOpen(false)
    onClose?.()
  }

  const handleSubmit = async () => {
    if (!content.trim() || content.trim().length < 5) {
      setError('反馈内容至少需要5个字符')
      return
    }

    setSubmitting(true)
    setError(null)

    try {
      await feedbackService.create({
        feedback_type: feedbackType,
        content: content.trim(),
        page_url: window.location.href,
        project_id: projectId,
        context_info: {
          userAgent: navigator.userAgent,
          screenWidth: window.screen.width,
          screenHeight: window.screen.height,
          timestamp: new Date().toISOString(),
        },
      })

      setShowSuccess(true)
      setContent('')
      setTimeout(() => {
        setShowSuccess(false)
        handleClose()
      }, 1500)
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      {/* 悬浮按钮 */}
      {!isControlled && !dismissed && (
        <div className="fixed bottom-6 right-6 z-40 group">
          <button
            onClick={handleOpen}
            className="w-12 h-12 bg-primary text-white text-xs font-medium rounded-full shadow-lg hover:bg-blue-600 transition-all hover:scale-105 flex items-center justify-center"
            title="意见反馈"
          >
            反馈
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation()
              setDismissed(true)
              dismissFeedbackFab()
            }}
            className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-gray-500 text-white rounded-full opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center hover:bg-gray-700"
            title="关闭反馈按钮"
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={3}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}

      {/* 遮罩层 */}
      {panelVisible && (
        <div
          className="fixed inset-0 z-40 bg-black/10 transition-opacity"
          onClick={handleClose}
        />
      )}

      {/* 反馈面板 */}
      <div
        ref={panelRef}
        className={`fixed bottom-6 right-6 z-50 w-[380px] max-h-[75vh] bg-white rounded-2xl shadow-2xl border border-gray-100 flex flex-col transition-all duration-300 origin-bottom-right ${
          panelVisible
            ? 'opacity-100 scale-100 translate-y-0'
            : 'opacity-0 scale-95 translate-y-4 pointer-events-none'
        }`}
      >
        {/* 标题栏 */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <h3 className="text-base font-semibold text-gray-900">意见反馈</h3>
          <button
            onClick={handleClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* 成功提示 */}
        {showSuccess ? (
          <div className="flex-1 flex flex-col items-center justify-center py-12 px-5">
            <div className="w-14 h-14 rounded-full bg-green-50 flex items-center justify-center mb-4">
              <svg className="w-7 h-7 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <p className="text-base font-medium text-gray-900">感谢您的反馈</p>
            <p className="text-sm text-gray-500 mt-1">我们会尽快处理</p>
          </div>
        ) : (
          <>
            {/* 面板内容 */}
            <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
              {/* 反馈类型 */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">反馈类型</label>
                <div className="flex flex-wrap gap-2">
                  {FEEDBACK_TYPES.map((t) => (
                    <button
                      key={t.key}
                      onClick={() => setFeedbackType(t.key)}
                      className={`px-3 py-1.5 text-sm rounded-full border transition-colors ${
                        feedbackType === t.key
                          ? 'bg-primary text-white border-primary'
                          : 'bg-white text-gray-600 border-gray-200 hover:border-primary hover:text-primary'
                      }`}
                    >
                      {t.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* 反馈内容 */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  反馈内容 <span className="text-red-400">*</span>
                </label>
                <textarea
                  value={content}
                  onChange={(e) => {
                    setContent(e.target.value)
                    if (error) setError(null)
                  }}
                  placeholder={PLACEHOLDER_MAP[feedbackType] || PLACEHOLDER_MAP.other}
                  rows={5}
                  maxLength={2000}
                  className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-lg resize-none focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-colors"
                />
                <div className="flex justify-between mt-1">
                  {error && <p className="text-xs text-red-500">{error}</p>}
                  <p className="text-xs text-gray-400 ml-auto">{content.length}/2000</p>
                </div>
              </div>
            </div>

            {/* 底部操作区 */}
            <div className="px-5 pb-4 pt-2 border-t border-gray-100 space-y-3">
              <button
                onClick={handleSubmit}
                disabled={submitting || !content.trim()}
                className="w-full py-2.5 text-sm font-medium text-white bg-primary rounded-lg hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {submitting ? '提交中...' : '提交反馈'}
              </button>

              {/* 微信紧急联系 */}
              <ServiceWechatContact />
            </div>
          </>
        )}
      </div>
    </>
  )
}

export default FeedbackPanel
