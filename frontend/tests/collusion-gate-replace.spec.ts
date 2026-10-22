import assert from 'node:assert/strict'
import test from 'node:test'

import { buildGateReplacePlan } from '../src/pages/collusionUploadParts/gateReplacePlan.ts'

// 门禁"返回更换文档"必须阻断全过程
// 历史故障(2026-07-23 真实项目):
// 项目2244: 招标文件可疑, 用户选"返回更换"后投标文件仍被解析,
// 破坏"招标文件作为白名单强化审查"的意图
// 项目2245: 一份投标可疑撤销后仅剩1份投标, 但剩余文档仍被直接解析,
// 绕过 isUploadAndProcessEnabled 的 MIN_BID>=2 门禁(该门禁只在按钮点击时生效)
// 约束: 本批全部文档(可疑+干净)一并撤销, 计划中不存在任何"继续解析"动作;
// 干净文件回到待上传态, 由下一轮"上传并处理"按钮门禁统一放行

test('场景2244: 招标可疑时, 干净投标文档必须一并撤销而非继续解析', () => {
  const plan = buildGateReplacePlan(
    [{ key: 'tender_1', type: 'tender', documentId: 100 }],
    [
      { key: 'bid_1', type: 'bid', documentId: 101 },
      { key: 'bid_2', type: 'bid', documentId: 102 },
    ],
  )
  // 全部3份文档从服务端删除(含干净投标, 阻断其解析)
  assert.deepEqual([...plan.deleteDocumentIds].sort(), [100, 101, 102])
  // 可疑招标清空待重新选择
  assert.equal(plan.clearTender, true)
  assert.equal(plan.resetTenderToPending, false)
  // 干净投标回到待上传态(非移除, 下轮重传)
  assert.deepEqual([...plan.resetBidKeysToPending].sort(), ['bid_1', 'bid_2'])
  assert.deepEqual(plan.removeBidKeys, [])
})

test('场景2245: 一份投标可疑撤销后, 干净的招标与投标同样撤销回待上传态', () => {
  const plan = buildGateReplacePlan(
    [{ key: 'bid_1', type: 'bid', documentId: 201 }],
    [
      { key: 'tender_1', type: 'tender', documentId: 200 },
      { key: 'bid_2', type: 'bid', documentId: 202 },
    ],
  )
  // 全部3份文档删除: 干净文档不得遗留为"已上传未解析"或被解析
  assert.deepEqual([...plan.deleteDocumentIds].sort(), [200, 201, 202])
  // 可疑投标移出列表(用户需补充新文件, 剩1份时 MIN_BID 门禁在下轮点击时生效)
  assert.deepEqual(plan.removeBidKeys, ['bid_1'])
  // 干净招标/投标回到待上传态
  assert.equal(plan.resetTenderToPending, true)
  assert.equal(plan.clearTender, false)
  assert.deepEqual(plan.resetBidKeysToPending, ['bid_2'])
})

test('全部文件均可疑时: 全删全移除, 无待重传项', () => {
  const plan = buildGateReplacePlan(
    [
      { key: 'tender_1', type: 'tender', documentId: 300 },
      { key: 'bid_1', type: 'bid', documentId: 301 },
    ],
    [],
  )
  assert.deepEqual([...plan.deleteDocumentIds].sort(), [300, 301])
  assert.equal(plan.clearTender, true)
  assert.deepEqual(plan.removeBidKeys, ['bid_1'])
  assert.equal(plan.resetTenderToPending, false)
  assert.deepEqual(plan.resetBidKeysToPending, [])
})

test('结构性护栏: 计划契约中不存在任何"继续解析"字段', () => {
  const plan = buildGateReplacePlan(
    [{ key: 'bid_1', type: 'bid', documentId: 400 }],
    [{ key: 'bid_2', type: 'bid', documentId: 401 }],
  )
  // 防止未来回归: 处置计划只允许 删除/移除/重置 三类动作
  assert.deepEqual(
    Object.keys(plan).sort(),
    [
      'clearTender',
      'deleteDocumentIds',
      'removeBidKeys',
      'resetBidKeysToPending',
      'resetTenderToPending',
    ],
  )
})
