import assert from 'node:assert/strict'
import test from 'node:test'

import { buildCollusionOverviewNotices } from '../src/pages/collusionResult/collusionOverviewNotices.ts'

test('低置信行业画像应降级为隐式保守口径说明', () => {
  const notices = buildCollusionOverviewNotices({
    dataQualityFlags: ['当前项目的对比覆盖率未达到 100%，整体结论需结合明细谨慎使用。'],
    profileQualityFlags: ['行业画像信号不足，当前行业结论已降级为低置信度/未知行业。'],
    industryProfileConfidence: 'low',
  })

  assert.equal(notices.length, 2)
  assert.match(notices[0], /覆盖率未达到 100%/)
  assert.match(notices[1], /未额外依赖稳定行业先验/)
  assert.equal(notices.some((item) => item.includes('行业画像信号不足')), false)
})

test('无附加说明时应返回空数组', () => {
  const notices = buildCollusionOverviewNotices({})
  assert.deepEqual(notices, [])
})
