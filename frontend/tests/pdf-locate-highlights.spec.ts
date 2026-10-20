import assert from 'node:assert/strict'
import test from 'node:test'

import {
  foldExcerpt,
  locateHighlightBbox,
  locateHighlights,
  type TextRegion,
} from '../src/pages/result/pdfPreview/locateHighlights.ts'

function region(text: string, bbox: [number, number, number, number]): TextRegion {
  return { text, bbox }
}

test('整段包含优先：摘录落在小框和大框时取面积更小的框', () => {
  const regions = [
    region('本项目投标人须提供有效的安全生产许可证原件扫描件', [0.05, 0.10, 0.95, 0.40]),
    region('安全生产许可证原件扫描件', [0.10, 0.20, 0.70, 0.28]),
  ]
  const bbox = locateHighlightBbox('安全生产许可证原件扫描件', regions)
  assert.deepEqual(bbox, [0.10, 0.20, 0.70, 0.28])
})

test('同形态第二例：证书名称摘录同样取最小完整包含框，而不是整段段落', () => {
  const regions = [
    region('拟派项目经理应具备一级建造师注册证书且在有效期内', [0.08, 0.50, 0.92, 0.80]),
    region('一级建造师注册证书', [0.12, 0.55, 0.48, 0.61]),
  ]
  const bbox = locateHighlightBbox('一级建造师注册证书', regions)
  assert.deepEqual(bbox, [0.12, 0.55, 0.48, 0.61])
})

test('禁止短 span 抢最小面积：真子集单字不单独中标，改为合并足够长的片段', () => {
  const regions = [
    region('投', [0.10, 0.20, 0.12, 0.22]),
    region('投标人须提供有效安全生产许可', [0.12, 0.20, 0.70, 0.28]),
    region('证', [0.70, 0.20, 0.74, 0.28]),
  ]
  const bbox = locateHighlightBbox('投标人须提供有效安全生产许可证', regions)
  assert.deepEqual(bbox, [0.12, 0.20, 0.70, 0.28])
})

test('折叠标点后仍能命中；定位失败不得退回整页框', () => {
  assert.equal(foldExcerpt('第 3 章……质量保证。'), '第3章质量保证')
  const regions = [
    region('第3章质量保证体系', [0.2, 0.3, 0.8, 0.36]),
  ]
  const hit = locateHighlightBbox('第 3 章……质量保证', regions)
  assert.deepEqual(hit, [0.2, 0.3, 0.8, 0.36])

  const miss = locateHighlights(9, ['正文中不存在的摘录XYZ'], regions)
  assert.deepEqual(miss, [])
})
