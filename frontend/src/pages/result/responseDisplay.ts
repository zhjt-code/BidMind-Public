/** 落库/接口短状态，只作识别，不直接展示给用户。 */
export const HIT_UNCONFIRMED_NOTE = '检索已命中待确认'
export const HIT_UNCONFIRMED_HEADER = '【检索已命中待确认】'
export const HIT_UNCONFIRMED_USER_LABEL = '相关材料待核对'
export const HIT_UNCONFIRMED_HINT = '以下为审查摘要，如需核实请点击相关页码查看原文'

export interface ResponseDisplayInput {
  responseSummary?: string | null
  responseContent?: string | null
  responseMissingElements?: string[]
  relevanceNote?: string | null
}

export interface ResponseDisplay {
  body: string
  statusLabel: string | null
  hint: string | null
  missingElements: string[]
}

function isHitUnconfirmedText(text: string | null | undefined): boolean {
  const value = (text || '').trimStart()
  return value.startsWith(HIT_UNCONFIRMED_HEADER) || value === HIT_UNCONFIRMED_NOTE
}

export function resolveResponseDisplay(input: ResponseDisplayInput): ResponseDisplay {
  const summary = (input.responseSummary || '').trim()
  const content = (input.responseContent || '').trim()
  const unconfirmed = (
    isHitUnconfirmedText(content)
    || isHitUnconfirmedText(summary)
    || input.relevanceNote === HIT_UNCONFIRMED_NOTE
    || (input.responseMissingElements || []).includes(HIT_UNCONFIRMED_NOTE)
  )
  const missingElements = (input.responseMissingElements || []).filter(
    (item) => item !== HIT_UNCONFIRMED_NOTE,
  )

  if (unconfirmed) {
    const body = summary && !isHitUnconfirmedText(summary)
      ? summary
      : HIT_UNCONFIRMED_USER_LABEL
    return {
      body,
      statusLabel: HIT_UNCONFIRMED_USER_LABEL,
      hint: HIT_UNCONFIRMED_HINT,
      missingElements,
    }
  }

  return {
    body: summary || content || '未响应',
    statusLabel: null,
    hint: null,
    missingElements,
  }
}
