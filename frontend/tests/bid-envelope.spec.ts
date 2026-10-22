import assert from 'node:assert/strict'
import test from 'node:test'

import {
  BID_ENVELOPE_PROFILES,
  DEFAULT_ENVELOPE_MODE,
  bidPartLabel,
  deriveBidReadiness,
  envelopeIsComplete,
  getEnvelopeProfile,
  inferEnvelopeModeFromDocuments,
} from '../src/lib/bidEnvelope.ts'
import type { BidEnvelopeProfile } from '../src/lib/bidEnvelope.ts'

// 同形态第二例: 两阶段招标第一/第二信封档案(禁止只测商务/技术实体词)
const SECOND_MORPH_PROFILES: BidEnvelopeProfile[] = [
  {
    mode: 'integral',
    label: '整本',
    slots: [{ id: 'integral', bidPart: null, label: '投标文件' }],
  },
  {
    mode: 'two_envelope',
    label: '第一信封 + 第二信封',
    slots: [
      { id: 'first_envelope', bidPart: 'first_envelope', label: '第一信封' },
      { id: 'second_envelope', bidPart: 'second_envelope', label: '第二信封' },
    ],
  },
]

test('默认模式为整本(老用户零感知)', () => {
  assert.equal(DEFAULT_ENVELOPE_MODE, 'integral')
  assert.equal(BID_ENVELOPE_PROFILES[0].slots.length, 1)
  assert.equal(BID_ENVELOPE_PROFILES[0].slots[0].bidPart, null)
})

test('整本单文档齐套, 分卷需商务+技术各一份', () => {
  assert.equal(envelopeIsComplete([{ bid_part: null }]), true)
  assert.equal(
    envelopeIsComplete([{ bid_part: 'commercial' }, { bid_part: 'technical' }]),
    true,
  )
  assert.equal(envelopeIsComplete([{ bid_part: 'commercial' }]), false)
  assert.equal(
    envelopeIsComplete([{ bid_part: 'commercial' }, { bid_part: 'commercial' }]),
    false,
  )
  assert.equal(envelopeIsComplete([{ bid_part: null }, { bid_part: 'commercial' }]), false)
  assert.equal(envelopeIsComplete([]), false)
})

test('从已有文档推断模式: 具名卷进分卷, 否则整本', () => {
  assert.equal(inferEnvelopeModeFromDocuments([]), 'integral')
  assert.equal(inferEnvelopeModeFromDocuments([{ bid_part: null }]), 'integral')
  assert.equal(
    inferEnvelopeModeFromDocuments([{ bid_part: 'technical' }]),
    'commercial_technical',
  )
})

test('就绪判定: 缺卷点名缺失卷, 未解析点名处理中卷', () => {
  const missing = deriveBidReadiness([{ bid_part: 'commercial', parsed: 1 }])
  assert.equal(missing.ready, false)
  assert.match(missing.message, /缺少技术标/)

  const parsing = deriveBidReadiness([
    { bid_part: 'commercial', parsed: 1 },
    { bid_part: 'technical', parsed: 0 },
  ])
  assert.equal(parsing.ready, false)
  assert.match(parsing.message, /技术标尚未处理完成/)

  const readyIntegral = deriveBidReadiness([{ bid_part: null, parsed: 1 }])
  assert.equal(readyIntegral.ready, true)

  const noDocs = deriveBidReadiness([])
  assert.equal(noDocs.ready, false)
  assert.match(noDocs.message, /缺少投标文档/)
})

test('整本未解析沿用既有文案主体(存量流程零变化)', () => {
  const parsing = deriveBidReadiness([{ bid_part: null, parsed: 0 }])
  assert.equal(parsing.ready, false)
  assert.match(parsing.message, /投标文件尚未处理完成/)
})

test('同形态第二例: 第一/第二信封档案同一机制齐套与就绪', () => {
  assert.equal(
    envelopeIsComplete(
      [{ bid_part: 'first_envelope' }, { bid_part: 'second_envelope' }],
      SECOND_MORPH_PROFILES,
    ),
    true,
  )
  assert.equal(
    inferEnvelopeModeFromDocuments(
      [{ bid_part: 'second_envelope' }],
      SECOND_MORPH_PROFILES,
    ),
    'two_envelope',
  )
  const missing = deriveBidReadiness(
    [{ bid_part: 'first_envelope', parsed: 1 }],
    SECOND_MORPH_PROFILES,
  )
  assert.equal(missing.ready, false)
  assert.match(missing.message, /缺少second_envelope/)
})

test('未登记卷角色标签回退原 token', () => {
  assert.equal(bidPartLabel('commercial'), '商务标')
  assert.equal(bidPartLabel('first_envelope'), 'first_envelope')
  assert.equal(bidPartLabel(null), '投标文件')
})

test('getEnvelopeProfile 未知模式回退默认档案', () => {
  assert.equal(getEnvelopeProfile('bogus').mode, 'integral')
  assert.equal(getEnvelopeProfile('commercial_technical').slots.length, 2)
})
