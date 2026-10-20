import React, { useEffect, useState } from 'react'

interface ModalProps {
  visible: boolean
  title?: string
  onClose?: () => void
  children: React.ReactNode
  footer?: React.ReactNode
  /** Tailwind max-width 类，默认 max-w-md */
  maxWidth?: string
  /** 点击遮罩是否关闭，默认 true */
  maskClosable?: boolean
}

const Modal: React.FC<ModalProps> = ({
  visible,
  title,
  onClose,
  children,
  footer,
  maxWidth = 'max-w-md',
  maskClosable = true,
}) => {
  // mounted 控制是否渲染 DOM；entering 控制 CSS 过渡类
  const [mounted, setMounted] = useState(false)
  const [entering, setEntering] = useState(false)

  useEffect(() => {
    if (visible) {
      setMounted(true)
      // 两帧延迟，确保初始 opacity-0/scale-95 先渲染，再触发过渡
      const raf = requestAnimationFrame(() =>
        requestAnimationFrame(() => setEntering(true))
      )
      return () => cancelAnimationFrame(raf)
    } else {
      setEntering(false)
      // 等动画结束后卸载 DOM（duration-200 = 200ms）
      const timer = setTimeout(() => setMounted(false), 220)
      return () => clearTimeout(timer)
    }
  }, [visible])

  // 按 Escape 关闭
  useEffect(() => {
    if (!visible || !onClose) return
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [visible, onClose])

  if (!mounted) return null

  return (
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center p-4 transition-opacity duration-200 ${
        entering ? 'opacity-100' : 'opacity-0'
      }`}
    >
      {/* 遮罩层 */}
      <div
        className="absolute inset-0 bg-black/50"
        onClick={maskClosable ? onClose : undefined}
      />

      {/* 弹窗主体 */}
      <div
        className={`relative bg-white rounded-xl shadow-2xl w-full ${maxWidth} max-h-[calc(100vh-2rem)] flex flex-col overflow-hidden transition-all duration-200 ${
          entering ? 'opacity-100 scale-100 translate-y-0' : 'opacity-0 scale-95 -translate-y-2'
        }`}
      >
        {/* 标题栏 */}
        {(title || onClose) && (
          <div className="shrink-0 flex items-center justify-between px-6 py-4 border-b">
            {title && (
              <h3 className="text-base font-semibold text-gray-900">{title}</h3>
            )}
            {onClose && (
              <button
                onClick={onClose}
                className="ml-auto p-1 text-gray-400 hover:text-gray-600 rounded transition-colors"
                aria-label="关闭"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>
        )}

        {/* 内容区 */}
        <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-6 py-5">{children}</div>

        {/* 底部操作区 */}
        {footer && (
          <div className="shrink-0 px-6 py-4 border-t flex justify-end gap-3">{footer}</div>
        )}
      </div>
    </div>
  )
}

export default Modal
