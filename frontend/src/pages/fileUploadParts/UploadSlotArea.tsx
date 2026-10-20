/**
 * 职责: 单个上传槽位的展示与交互(拖拽/选择/已有文档/进度/更换入口)
 * 不做: 不发请求、不轮询、不管理跨槽位状态与页面导航
 *
 * 槽位身份(招标/整本/商务标/技术标)由 props 注入；卷宗标题由外壳负责。
 */
import React from 'react'
import { toast } from 'sonner'
import FileProcessProgress from '@/components/FileProcessProgress'
import type { ProcessStatusData } from '@/services/documentApi'
import { formatFileSize, formatDate } from '@/lib/formatters'
import { resolveDocumentProcessFailureMessage } from '@/lib/formatFailureMessage'
import {
  UPLOAD_ACCEPT,
  UPLOAD_FORMAT_HINT,
  UPLOAD_FORMAT_TOAST,
  isAllowedUploadFile,
} from '@/lib/uploadAccept'
import TruncatedHoverText from '@/components/upload/TruncatedHoverText'
import { formatExistingDocumentStatusLabel } from './existingDocFailure'

export interface EnvelopeUploadedFile {
  file: File
  name: string
  status: 'pending' | 'uploading' | 'processing' | 'success' | 'error'
  documentId?: number
  message?: string
}

export interface ExistingDocument {
  id: number
  filename: string
  file_size: number
  created_at: string
  parsed: number
  /** : 文档处理失败原因（轮询/status 回填） */
  lastError?: string
}

export interface UploadSlotAreaProps {
  slotId: string
  label: string
  /** 拖拽提示中的文档名(招标文件/投标文件/商务标/技术标) */
  docName: string
  file: EnvelopeUploadedFile | null
  existingDoc: ExistingDocument | null
  processStatus: ProcessStatusData | null
  existingProcessStatus: ProcessStatusData | null
  isDragging: boolean
  /** 本槽位处于单侧更换中(: 不清空对侧) */
  isReplacing: boolean
  isUploading: boolean
  isSomethingLoading: boolean
  onDraggingChange: (dragging: boolean) => void
  /** 通过拖拽或选择器选中合法文件后回调(校验已在组件内完成) */
  onFileChosen: (file: File) => void
  onStartReplace: () => void
  onCancelSelect: () => void
  /** 卷宗外壳已有标题时隐藏槽位 h2 */
  showHeading?: boolean
}

const getStatusIcon = (status: string) => {
  switch (status) {
    case 'success':
      return (
        <svg className="w-5 h-5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      )
    case 'error':
      return (
        <svg className="w-5 h-5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      )
    case 'uploading':
    case 'processing':
      return <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-blue-500" />
    default:
      return null
  }
}

const getStatusText = (uploadedFile: EnvelopeUploadedFile | null) => {
  if (!uploadedFile) return ''
  switch (uploadedFile.status) {
    case 'uploading': return '上传中...'
    case 'processing': return uploadedFile.message || '处理中...'
    case 'success': return '完成'
    case 'error': return uploadedFile.message || '失败'
    default: return ''
  }
}

const UploadSlotArea: React.FC<UploadSlotAreaProps> = ({
  slotId,
  label,
  docName,
  file,
  existingDoc,
  processStatus,
  existingProcessStatus,
  isDragging,
  isReplacing,
  isUploading,
  isSomethingLoading,
  onDraggingChange,
  onFileChosen,
  onStartReplace,
  onCancelSelect,
  showHeading = true,
}) => {
  // 用三元保留 ExistingDocument 窄化；Boolean(...) 会丢类型导致 TS18047
  const existingVisible =
    existingDoc !== null && !file && !isReplacing ? existingDoc : null
  const showExisting = existingVisible !== null

  // 上传区域 - 仅在无文件时才响应拖拽
  const canDrop = !file && !showExisting && !isUploading

  const dragBorderClass = isDragging && canDrop
    ? 'border-primary bg-blue-50 scale-[1.01]'
    : file?.status === 'error'
    ? 'border-red-300 bg-red-50'
    : file?.status === 'success'
    ? 'border-green-300 bg-green-50'
    : file?.status === 'processing'
    ? 'border-blue-300 bg-blue-50'
    : existingVisible?.parsed === -1
    ? 'border-red-300 bg-red-50'
    : existingVisible?.parsed === 1
    ? 'border-green-300 bg-green-50'
    : showExisting
    ? 'border-blue-300 bg-blue-50'
    : 'border-gray-300 hover:border-blue-400'

  const chooseFile = (chosen: File | undefined | null) => {
    if (!chosen) return
    if (!isAllowedUploadFile(chosen.name)) {
      toast.warning(UPLOAD_FORMAT_TOAST)
      return
    }
    onFileChosen(chosen)
  }

  return (
    <div>
      {showHeading && (
        <h3 className="text-base font-semibold text-gray-800 mb-3">{label}</h3>
      )}
      {isReplacing && (
        <p className="mb-2 text-sm text-amber-700">
          正在更换本文件，其余已上传文件将保留。选择新文件后点击「上传并处理该文件」。
        </p>
      )}
      <div
        className={`border-2 border-dashed rounded-lg p-6 transition-all duration-150 ${dragBorderClass}`}
        onDragOver={(e) => {
          if (!canDrop) return
          e.preventDefault()
          e.dataTransfer.dropEffect = 'copy'
          onDraggingChange(true)
        }}
        onDragLeave={(e) => {
          if (e.currentTarget.contains(e.relatedTarget as Node)) return
          onDraggingChange(false)
        }}
        onDrop={(e) => {
          e.preventDefault()
          onDraggingChange(false)
          if (isUploading || !canDrop) return
          chooseFile(e.dataTransfer.files?.[0])
        }}
      >
        {existingVisible ? (
          <div>
            <div className="flex items-center justify-center space-x-2 mb-2">
              {existingVisible.parsed === 1
                ? <svg className="w-5 h-5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                : existingVisible.parsed === -1
                ? <svg className="w-5 h-5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
                : <svg className="w-5 h-5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
              }
              <TruncatedHoverText text={existingVisible.filename} className="text-gray-700 font-medium" />
            </div>
            <div className="text-sm text-gray-500 space-y-1">
              <p>大小: {formatFileSize(existingVisible.file_size)}</p>
              <p>上传时间: {formatDate(existingVisible.created_at)}</p>
              <p>状态: {formatExistingDocumentStatusLabel(existingVisible.parsed)}</p>
              {existingVisible.parsed === -1 && (
                <p className="text-red-700">
                  原因: {resolveDocumentProcessFailureMessage(existingVisible.lastError)}
                </p>
              )}
            </div>
            {existingVisible.parsed === 0 && (
              <FileProcessProgress processStatus={existingProcessStatus} />
            )}
            {!isSomethingLoading && (
              <button
                onClick={onStartReplace}
                className="mt-3 text-sm text-blue-600 hover:text-blue-800"
              >
                {existingVisible.parsed === -1 ? '更换本文件' : '重新上传'}
              </button>
            )}
          </div>
        ) : file ? (
          <div>
            <div className="flex items-center justify-center space-x-3">
              <TruncatedHoverText text={file.name} className="text-gray-700 truncate max-w-xs" />
              {getStatusIcon(file.status)}
              <span className="text-sm text-gray-500">{getStatusText(file)}</span>
            </div>

            {file.status === 'processing' && (
              <FileProcessProgress processStatus={processStatus} />
            )}

            {file.status === 'pending' && !isSomethingLoading && (
              <div className="flex justify-center">
                <button
                  onClick={onCancelSelect}
                  className="mt-2 text-sm text-gray-500 hover:text-red-500"
                >
                  {isReplacing ? '取消更换' : '取消选择'}
                </button>
              </div>
            )}
          </div>
        ) : !isSomethingLoading ? (
          <div className="text-center">
            {isDragging && canDrop ? (
              <>
                <svg className="w-10 h-10 mx-auto mb-2 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                </svg>
                <p className="text-primary font-medium">松开鼠标即可上传</p>
              </>
            ) : (
              <>
                <svg className="w-10 h-10 mx-auto mb-2 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                </svg>
                <p className="text-gray-500 mb-3">
                  将{docName}（PDF/Word）拖拽到此处，或
                </p>
                <input
                  type="file" accept={UPLOAD_ACCEPT}
                  onChange={(e) => {
                    chooseFile(e.target.files?.[0])
                    e.target.value = ''
                  }}
                  className="hidden" id={`${slotId}-file`}
                />
                <label
                  htmlFor={`${slotId}-file`}
                  className="inline-block px-4 py-2 rounded-md cursor-pointer bg-primary text-white hover:bg-blue-600"
                >
                  点击选择文件
                </label>
                <p className="text-xs text-gray-400 mt-2">{UPLOAD_FORMAT_HINT}</p>
              </>
            )}
          </div>
        ) : null}
      </div>
    </div>
  )
}

export default UploadSlotArea
