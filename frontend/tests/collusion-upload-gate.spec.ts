import assert from 'node:assert/strict'
import test from 'node:test'

import { isUploadAndProcessEnabled } from '../src/pages/collusionUploadState.ts'

// 防御 "上传并处理" 按钮门禁与业务下限(MIN_BID=2)对齐
// 历史故障: 红字提示 "至少需要 2 份投标文件" 与按钮可点击状态并存,
// 用户上传 1 份即可点击, 处理完后 canStartAnalysis 永远不满足.

const MIN_BID = 2

test('totalBidCount < MIN_BID 时按钮必须禁用 (只有 1 份 pending 投标)', () => {
  const enabled = isUploadAndProcessEnabled({
    isUploading: false,
    isAnyProcessing: false,
    totalBidCount: 1,
    minBid: MIN_BID,
  })
  assert.equal(enabled, false)
})

test('totalBidCount === MIN_BID 且无上传/处理中时按钮启用 (2 份 pending 投标)', () => {
  const enabled = isUploadAndProcessEnabled({
    isUploading: false,
    isAnyProcessing: false,
    totalBidCount: 2,
    minBid: MIN_BID,
  })
  assert.equal(enabled, true)
})

test('已有 1 份解析完成 + 新增 1 份 pending 凑齐 MIN_BID 时按钮启用', () => {
  // 模拟: existingBids.length=1, bidFiles=[1 pending] -> totalBidCount=2
  const enabled = isUploadAndProcessEnabled({
    isUploading: false,
    isAnyProcessing: false,
    totalBidCount: 2,
    minBid: MIN_BID,
  })
  assert.equal(enabled, true)
})

test('totalBidCount=0 时按钮必须禁用 (仅有招标 pending, 无投标)', () => {
  // 边界: hasPendingFiles=true (tenderFile pending) 但 totalBidCount=0
  // 按钮会显示但应禁用, 避免用户误点
  const enabled = isUploadAndProcessEnabled({
    isUploading: false,
    isAnyProcessing: false,
    totalBidCount: 0,
    minBid: MIN_BID,
  })
  assert.equal(enabled, false)
})

test('isUploading=true 时按钮禁用 (即使 totalBidCount 已达标)', () => {
  const enabled = isUploadAndProcessEnabled({
    isUploading: true,
    isAnyProcessing: false,
    totalBidCount: 3,
    minBid: MIN_BID,
  })
  assert.equal(enabled, false)
})

test('isAnyProcessing=true 时按钮禁用 (即使 totalBidCount 已达标)', () => {
  const enabled = isUploadAndProcessEnabled({
    isUploading: false,
    isAnyProcessing: true,
    totalBidCount: 5,
    minBid: MIN_BID,
  })
  assert.equal(enabled, false)
})

test('totalBidCount 远超 MIN_BID (典型 5 份) 时按钮启用', () => {
  const enabled = isUploadAndProcessEnabled({
    isUploading: false,
    isAnyProcessing: false,
    totalBidCount: 5,
    minBid: MIN_BID,
  })
  assert.equal(enabled, true)
})
