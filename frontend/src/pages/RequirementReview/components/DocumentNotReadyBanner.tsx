/**
 * 招标 / 投标文档未就绪的提示条 (单条文档级)。
 *
 * 由调用方传入 visible=true 控制是否渲染。
 * 点击"返回文件上传页面"链接触发 onNavigateUpload 回调。
 */
import React from 'react'

export interface DocumentNotReadyBannerProps {
  visible: boolean
  message: string
  onNavigateUpload: () => void
}

export const DocumentNotReadyBanner: React.FC<DocumentNotReadyBannerProps> = ({
  visible,
  message,
  onNavigateUpload,
}) => {
  if (!visible) return null
  return (
    <div className="mb-4 p-4 bg-amber-50 rounded-lg border border-amber-300 flex items-start">
      <svg
        className="w-5 h-5 text-amber-500 mt-0.5 mr-2 shrink-0"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
        />
      </svg>
      <div>
        <p className="text-sm font-medium text-amber-800">{message}</p>
        <button
          onClick={onNavigateUpload}
          className="mt-2 text-sm text-amber-700 underline hover:text-amber-900"
        >
          返回文件上传页面
        </button>
      </div>
    </div>
  )
}
