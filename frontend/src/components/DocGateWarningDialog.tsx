import React from 'react'
import TruncatedHoverText from '@/components/upload/TruncatedHoverText'
import Modal from './Modal'
import type { DocGateResult, DocGateVerdict } from '@/services/documentApi'

// 文档门禁警告弹窗: 上传后、解析前展示可疑文档的怀疑理由，
// 提供"继续试用"与"返回更换文档"两个选择，不做阻断。

export interface GateWarningItem {
  filename: string
  gateResult: DocGateResult
}

interface DocGateWarningDialogProps {
  open: boolean
  items: GateWarningItem[]
  /** 处理中(禁用按钮防重复点击) */
  busy?: boolean
  onContinue: () => void
  onReplace: () => void
}

const USABILITY_VERDICTS: ReadonlySet<DocGateVerdict> = new Set([
  'encrypted',
  'corrupt',
  'empty',
])

const USABILITY_TITLE: Record<'encrypted' | 'corrupt' | 'empty', string> = {
  encrypted: '提示：文档已加密，暂时无法读取',
  corrupt: '提示：文档无法正常打开',
  empty: '提示：文档内容为空',
}

const USABILITY_INTRO: Record<'encrypted' | 'corrupt' | 'empty', string> = {
  encrypted: '以下文档需要密码才能打开，系统无法在加密状态下读取内容：',
  corrupt: '以下文档无法被正常打开，文件可能已损坏或不是有效的 PDF：',
  empty: '以下文档所有页面均未检测到文字、表格或图片，内容可能为空：',
}

function resolveUsabilityKind(
  items: GateWarningItem[],
): 'encrypted' | 'corrupt' | 'empty' | null {
  if (items.length === 0) return null
  if (!items.every((item) => USABILITY_VERDICTS.has(item.gateResult.verdict))) {
    return null
  }
  const kinds = new Set(items.map((item) => item.gateResult.verdict))
  if (kinds.size === 1) {
    return items[0].gateResult.verdict as 'encrypted' | 'corrupt' | 'empty'
  }
  return null
}

const DocGateWarningDialog: React.FC<DocGateWarningDialogProps> = ({
  open,
  items,
  busy = false,
  onContinue,
  onReplace,
}) => {
  const usabilityKind = resolveUsabilityKind(items)
  const allUsability = items.length > 0 && items.every(
    (item) => USABILITY_VERDICTS.has(item.gateResult.verdict),
  )
  const allUnreadable = items.length > 0 && items.every(
    (item) => item.gateResult.verdict === 'unreadable',
  )

  const title = usabilityKind
    ? USABILITY_TITLE[usabilityKind]
    : allUsability
      ? '提示：文档可能无法正常读取'
      : allUnreadable
        ? '提示：暂时无法识别文档的文字内容'
        : '提示：文档可能不是完整的招标/投标文件'

  const intro = usabilityKind
    ? USABILITY_INTRO[usabilityKind]
    : allUsability
      ? '系统对以下文档进行了快速检查，发现其可能已加密、损坏或内容为空：'
      : allUnreadable
        ? '以下文档可能是扫描件或图片型 PDF，系统未能从中提取到足够的文字信息，无法确认其是否为完整的招标/投标文件：'
        : '系统对以下文档进行了初步识别，发现其可能缺少完整招标/投标文件的常见结构或关键要素：'

  const continueLabel = allUnreadable && !allUsability
    ? '确认是标书文件，继续'
    : '我已了解，继续试用'

  const footerHint = allUsability
    ? '建议更换为可正常打开且含有效内容的招标/投标文件。若您确认仍要继续，后续处理仍可能因文件不可用而失败。'
    : allUnreadable
      ? '如果这确实是您的标书文件，可以继续处理；如果只是随手选择的文档，建议更换为真实的招标/投标文件。'
      : '建议您更换一份真实的招标或投标文件后再开始处理。当然，您也可以选择继续。'

  return (
    <Modal visible={open} title={title} maskClosable={false} maxWidth="max-w-lg" footer={
      <>
        <button
          onClick={onContinue}
          disabled={busy}
          className="px-4 py-2 rounded-md text-sm border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {continueLabel}
        </button>
        <button
          onClick={onReplace}
          disabled={busy}
          className="px-4 py-2 rounded-md text-sm bg-primary text-white hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          返回更换文档
        </button>
      </>
    }>
      <div className="space-y-4">
        <p className="text-sm text-gray-800 leading-relaxed">
          {intro}
        </p>

        <div className="space-y-3">
          {items.map((item) => (
            <div
              key={`${item.filename}_${item.gateResult.doc_type}`}
              className="p-3 bg-amber-50 border border-amber-200 rounded-lg"
            >
              <TruncatedHoverText text={item.filename} className="block text-sm font-medium text-amber-900 truncate" />
              <ul className="mt-1.5 space-y-1">
                {item.gateResult.reasons.map((reason) => (
                  <li key={reason} className="text-xs text-amber-800 leading-relaxed flex items-start">
                    <span className="mr-1.5 mt-0.5 shrink-0">-</span>
                    <span>{reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <p className="text-sm text-gray-600 leading-relaxed">
          本系统的价值在于对真实招标/投标文件进行专业审查与分析。
          若使用非标书类文档试用，分析结果将缺乏参考意义，也会占用您宝贵的免费试用额度，
          无法充分体验系统的完整能力。
        </p>
        <p className="text-sm text-gray-600 leading-relaxed">
          {footerHint}
        </p>
      </div>
    </Modal>
  )
}

export default DocGateWarningDialog
