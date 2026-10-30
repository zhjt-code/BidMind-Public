/**
 * 串标上传页"重名文件警告"对话框子组件（CollusionUpload 私有）。
 *
 * 抽自 CollusionUpload.tsx 末尾的 Modal 渲染段。与 checkDuplicateFiles 纯函数
 * 一起按 §1.2 决议落地（不抽 hook，仅纯函数 + 子组件双拆）。
 */
import React from 'react'
import { Modal } from '@/components'
import TruncatedHoverText from '@/components/upload/TruncatedHoverText'

export interface DuplicateWarningDialogProps {
  open: boolean
  duplicateNames: string[]
  onConfirm: () => void
  onCancel: () => void
}

export const DuplicateWarningDialog: React.FC<DuplicateWarningDialogProps> = ({
  open,
  duplicateNames,
  onConfirm,
  onCancel,
}) => {
  return (
    <Modal
      visible={open}
      title="检测到重名文件"
      maskClosable={false}
      footer={
        <div className="flex gap-3 justify-end">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-gray-600 bg-gray-100 rounded-md hover:bg-gray-200 text-sm"
          >
            取消
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 bg-orange-500 text-white rounded-md hover:bg-orange-600 text-sm"
          >
            仍然上传
          </button>
        </div>
      }
    >
      <div className="space-y-3">
        <div className="flex items-start gap-2 p-3 bg-orange-50 border border-orange-200 rounded-lg">
          <svg
            className="w-5 h-5 text-orange-500 shrink-0 mt-0.5"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
            />
          </svg>
          <div>
            <p className="text-sm text-orange-800 font-medium">以下文件名与已有文件重复:</p>
            <ul className="mt-1 text-sm text-orange-700">
              {duplicateNames.map((name, i) => (
                <li key={i} className="flex min-w-0">
                  <span className="shrink-0">- </span>
                  <TruncatedHoverText text={name} className="min-w-0 truncate" />
                </li>
              ))}
            </ul>
          </div>
        </div>
        <p className="text-sm text-gray-600">
          重名文件可能导致分析结果中无法区分不同投标人，建议修改文件名后重新上传。
        </p>
        <p className="text-sm text-gray-500">
          如果您确定这些是不同投标人的文件，可以点击"仍然上传"继续。
        </p>
      </div>
    </Modal>
  )
}

export default DuplicateWarningDialog
