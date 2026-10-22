/**
 * 职责: 投标信封前端契约——模式档案、模式推断、齐套判定、投标侧就绪派生
 * 不做: 不发请求、不渲染 UI、不持有 React 状态
 *
 * 与后端 core/bid_envelope.py 同构: 信封 = 1 份整本(bid_part=null)
 * 或一组分卷(商务标+技术标)。档案数据驱动, 新形态(第一/第二信封等)
 * 只需加档案, 判定与展示逻辑不改。
 */

export interface BidEnvelopeSlot {
  /** 槽位 id(整本为 'integral', 分卷与 bid_part 同名) */
  id: string
  /** 上传时提交的 bid_part(整本为 null) */
  bidPart: string | null
  /** 槽位标题 */
  label: string
}

export interface BidEnvelopeProfile {
  /** 模式名: integral / commercial_technical */
  mode: string
  /** 模式切换控件文案 */
  label: string
  slots: BidEnvelopeSlot[]
}

/** 卷角色中文标签; 未登记角色回退原 token, 不阻断渲染 */
export const bidPartLabels: Record<string, string> = {
  commercial: '商务标',
  technical: '技术标',
}

export const bidPartLabel = (part: string | null | undefined): string =>
  part ? bidPartLabels[part] || part : '投标文件'

export const BID_ENVELOPE_PROFILES: BidEnvelopeProfile[] = [
  {
    mode: 'integral',
    label: '整本投标文件',
    slots: [{ id: 'integral', bidPart: null, label: '投标文件 (PDF/Word)' }],
  },
  {
    mode: 'commercial_technical',
    label: '商务标 + 技术标',
    slots: [
      { id: 'commercial', bidPart: 'commercial', label: '商务标 (PDF/Word)' },
      { id: 'technical', bidPart: 'technical', label: '技术标 (PDF/Word)' },
    ],
  },
]

export const DEFAULT_ENVELOPE_MODE = BID_ENVELOPE_PROFILES[0].mode

/** 文档列表 API 返回的 BID 文档最小结构 */
export interface BidDocumentLike {
  bid_part?: string | null
  parsed?: number
}

export function getEnvelopeProfile(
  mode: string,
  profiles: BidEnvelopeProfile[] = BID_ENVELOPE_PROFILES,
): BidEnvelopeProfile {
  return profiles.find((p) => p.mode === mode) || profiles[0]
}

/** 从已有 BID 文档推断信封模式(任一具名卷即分卷; 无文档默认整本) */
export function inferEnvelopeModeFromDocuments(
  bids: BidDocumentLike[],
  profiles: BidEnvelopeProfile[] = BID_ENVELOPE_PROFILES,
): string {
  for (const bid of bids) {
    const part = bid.bid_part ?? null
    if (part === null) continue
    const owner = profiles.find((p) => p.slots.some((s) => s.bidPart === part))
    if (owner) return owner.mode
  }
  return profiles[0].mode
}

/** 信封是否齐套: 卷角色多重集与某一档案完全一致(缺卷/重卷/混装均否) */
export function envelopeIsComplete(
  bids: BidDocumentLike[],
  profiles: BidEnvelopeProfile[] = BID_ENVELOPE_PROFILES,
): boolean {
  if (bids.length === 0) return false
  const actual = bids.map((b) => b.bid_part ?? '').sort()
  return profiles.some((profile) => {
    const expected = profile.slots.map((s) => s.bidPart ?? '').sort()
    return (
      expected.length === actual.length &&
      expected.every((v, i) => v === actual[i])
    )
  })
}

export interface BidReadiness {
  /** true=就绪 / false=缺失或处理中 */
  ready: boolean
  /** ready=false 时的用户提示 */
  message: string
}

/** 投标侧就绪判定(供要求确认页等下游消费), 文案点名缺失/未完成的卷 */
export function deriveBidReadiness(
  bids: BidDocumentLike[],
  profiles: BidEnvelopeProfile[] = BID_ENVELOPE_PROFILES,
): BidReadiness {
  if (bids.length === 0) {
    return {
      ready: false,
      message: '项目缺少投标文档，请先返回上传页面上传投标文件',
    }
  }
  if (!envelopeIsComplete(bids, profiles)) {
    const mode = inferEnvelopeModeFromDocuments(bids, profiles)
    const profile = getEnvelopeProfile(mode, profiles)
    const present = new Set(bids.map((b) => b.bid_part ?? null))
    const missing = profile.slots
      .filter((s) => !present.has(s.bidPart))
      .map((s) => bidPartLabel(s.bidPart))
    const detail = missing.length > 0 ? `（缺少${missing.join('、')}）` : ''
    return {
      ready: false,
      message: `投标文件未齐套${detail}，请返回上传页补齐`,
    }
  }
  const unparsed = bids.filter((b) => b.parsed !== 1)
  if (unparsed.length > 0) {
    const names = unparsed.map((b) => bidPartLabel(b.bid_part ?? null))
    return {
      ready: false,
      message: `${names.join('、')}尚未处理完成，请等待处理完成后再操作`,
    }
  }
  return { ready: true, message: '' }
}
