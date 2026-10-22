import type { CollusionPairResult } from '@/services/collusionApi'

export interface PairOverviewMessage {
  tone: 'muted' | 'warning' | 'danger' | 'info'
  text: string
}

export function buildPairOverviewMessages(
  pair: Pick<
    CollusionPairResult,
    'analysisMode' | 'decisionState' | 'reviewState' | 'similarityScore'
  >,
): PairOverviewMessage[] {
  const messages: PairOverviewMessage[] = []

  if (pair.decisionState === 'failed' || pair.reviewState === 'failed') {
    return [
      {
        tone: 'danger',
        text: '本次自动分析未能形成稳定结果，请结合原文人工复核。',
      },
      {
        tone: 'muted',
        text: '常见原因包括文件无法正常解析、关键信息提取失败，或分析过程中出现异常。',
      },
    ]
  }

  if (pair.decisionState === 'skipped') {
    return [
      {
        tone: 'warning',
        text: '系统尚未对这对文件完成深入分析，目前结果仅供参考，不能直接等同于无风险。',
      },
      {
        tone: 'muted',
        text: '建议结合原文人工复核；如有需要，可重新发起分析。',
      },
    ]
  }

  if (pair.decisionState === 'needs_review') {
    if (pair.analysisMode === 'algorithm_only') {
      messages.push({
        tone: 'warning',
        text: '当前未发现异常证据，但系统只完成了基础核查，暂不能直接判定为无风险。',
      })
      messages.push({
        tone: 'muted',
        text: '系统未能从文件中提取到足够完整、稳定的关键信息。常见原因包括文件为扫描件、文本层缺失、版式复杂，或关键内容主要存在于图片、表格中。',
      })
      messages.push({
        tone: 'info',
        text: '请核实原文件是否清晰可选中，并重点检查人员、企业、报价等关键信息；必要时可重新上传后再次分析。',
      })
      return messages
    }

    if (pair.analysisMode?.includes('degraded')) {
      return [
        {
          tone: 'warning',
          text: '当前未发现异常证据，但部分关键信息提取不完整，系统暂不能直接判定为无风险。',
        },
        {
          tone: 'muted',
          text: '常见原因包括文件为扫描件、文本层缺失、版式复杂，或关键内容主要存在于图片、表格中。',
        },
        {
          tone: 'info',
          text: '建议核实原文件清晰度与可选中文本，并结合原文人工复核。',
        },
      ]
    }

    return [
      {
        tone: 'warning',
        text: '当前未发现异常证据，但系统未能形成足够完整的分析结果，因此暂不能直接判定为无风险。',
      },
      {
        tone: 'muted',
        text: '建议结合原文人工复核，重点查看人员信息、企业信息和核心方案内容。',
      },
    ]
  }

  if (pair.analysisMode?.includes('recovered')) {
    messages.push({
      tone: 'info',
      text: '系统已补充完成部分维度分析，建议结合原文核实关键结论。',
    })
  }

  if (pair.similarityScore !== undefined && pair.similarityScore >= 0.7) {
    messages.push({
      tone: 'muted',
      text: `文本相似度约 ${Math.round(pair.similarityScore * 100)}%，请结合具体证据判断这些重合是否属于模板或通用表述。`,
    })
  }

  return messages
}
