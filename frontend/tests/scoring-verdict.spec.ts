/**
 * verdict 单源防御测试：评分横幅三态必须消费后端 scoring_verdict 权威结论。
 *
 * 覆盖：
 * ① transformScoringVerdict：合法三值原样返回；非法值/null/undefined 返回 null
 * ② verdict='amber' 而本地输入为绿（report needsReview=false 且无 ledger）→ 容器 amber：
 * 通过判定与三态均以 verdict 为权威（isOk/visualState 定义锁定），amber 态容器类
 * 含 bg-amber-50
 * ③ verdict='green' 而本地输入为 amber（如 ledger review_required）→ 容器 green 且
 * 不渲染 amber 复核区（复核块由 !isOk 门控，verdict 权威 green 时让位）
 * ④ 不传 verdict 时行为不变：isOk/visualState 的本地回退公式与历史版本逐 token 一致
 * ⑤ 数据接线：index.tsx 首载/手动刷新/轮询回调三路 + 重提取清空 + 轮询 hook 透传
 *
 * 说明：本仓库测试用 node --test（无 JSX 渲染设施），组件行为按邻近 spec 的既有模式以
 * 源码契约锁定（transform 纯函数直接单测）。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { transformScoringVerdict } from '../src/pages/RequirementReview/scoringVerdict.ts'

const bannerSource = readFileSync(
  new URL('../src/pages/RequirementReview/components/ScoringTotalsBanner.tsx', import.meta.url),
  'utf8',
)

const pageSource = readFileSync(
  new URL('../src/pages/RequirementReview/index.tsx', import.meta.url),
  'utf8',
)

const pollingSource = readFileSync(
  new URL(
    '../src/pages/RequirementReview/hooks/useRequirementExtractionPolling.ts',
    import.meta.url,
  ),
  'utf8',
)

// ===== ① transformScoringVerdict 合法值/非法值/null =====

test('transformScoringVerdict 合法三值原样返回', () => {
  assert.equal(transformScoringVerdict('green'), 'green')
  assert.equal(transformScoringVerdict('blue'), 'blue')
  assert.equal(transformScoringVerdict('amber'), 'amber')
})

test('transformScoringVerdict 非法值一律返回 null', () => {
  assert.equal(transformScoringVerdict(null), null)
  assert.equal(transformScoringVerdict(undefined), null)
  assert.equal(transformScoringVerdict(''), null)
  assert.equal(transformScoringVerdict('GREEN'), null, '大小写不匹配不得放行')
  assert.equal(transformScoringVerdict('red'), null, '枚举外字符串必须拒绝')
  assert.equal(transformScoringVerdict(' green'), null, '含空白不得放行')
  assert.equal(transformScoringVerdict(0), null)
  assert.equal(transformScoringVerdict(true), null)
  assert.equal(transformScoringVerdict({}), null)
  assert.equal(transformScoringVerdict(['green']), null)
})

// ===== ② verdict='amber' 压过本地绿：三态与通过判定均以 verdict 为权威 =====

test('isOk 以 verdict 为权威：非空时 verdict === green 才算通过', () => {
  const isOkMatch = bannerSource.match(/const\s+isOk\s*=\s*[^\n]+/)
  assert.ok(isOkMatch, 'isOk 定义必须存在')
  assert.match(
    isOkMatch![0],
    /verdict\s*!=\s*null\s*\?\s*verdict\s*===\s*'green'/,
    "verdict 非空时 isOk 必须直接取 verdict === 'green'（verdict='amber' 时本地绿不得判通过）",
  )
})

test('visualState 以 verdict 为权威：非空时直接接管三态', () => {
  const match = bannerSource.match(/const\s+visualState[\s\S]{0,200}/)
  assert.ok(match, 'visualState 定义必须存在')
  assert.match(
    match![0],
    /verdict\s*\?\?/,
    "visualState 必须为 verdict ?? (本地推导)，verdict='amber' 时容器必为 amber",
  )
})

test('amber 态容器类含 bg-amber-50（visualState 非 green 非 blue 的兜底分支）', () => {
  const containerMatch = bannerSource.match(/const\s+containerClass\s*=[\s\S]{0,400}/)
  assert.ok(containerMatch, 'containerClass 定义必须存在')
  assert.match(
    containerMatch![0],
    /visualState\s*===\s*'green'/,
    'containerClass 必须由 visualState 驱动',
  )
  assert.match(containerMatch![0], /bg-amber-50/, 'amber 态容器必须使用 bg-amber-50')
  assert.match(containerMatch![0], /bg-green-50/, 'green 态容器必须使用 bg-green-50')
})

// ===== ③ verdict='green' 压过本地 amber：容器 green 且不渲染 amber 复核区 =====

test('账本复核块由 !isOk 门控：verdict 权威 green 时让位', () => {
  assert.match(
    bannerSource,
    /\{!isOk && effectiveLedgerNeedsReview && ledgerReport && \(/,
    'amber 复核区必须含 !isOk 门控，verdict=green 时不得在绿色容器内渲染琥珀告警',
  )
})

test('verdict=green 时问题明细整体让位（isOk 门保持由权威 isOk 驱动）', () => {
  assert.match(
    bannerSource,
    /\{isOk \? null : \(/,
    'legacy 问题明细必须由 isOk 门控；isOk 已含 verdict 权威语义',
  )
})

// ===== ④ 不传 verdict 时行为不变：本地回退公式逐 token 一致 =====

test('isOk 本地回退公式与历史版本一致（四个判据齐全）', () => {
  const isOkMatch = bannerSource.match(/const\s+isOk\s*=\s*[^\n]+/)
  assert.ok(isOkMatch, 'isOk 定义必须存在')
  assert.match(
    isOkMatch![0],
    /:\s*!effectiveReportNeedsReview\s*&&\s*!effectiveLedgerNeedsReview\s*&&\s*!scopeAmber\s*&&\s*!isScopeInfoOnly/,
    'verdict 缺失时必须完整回退到原有四判据公式，不得增删',
  )
})

test('visualState 本地回退保持原有三态推导', () => {
  const match = bannerSource.match(/const\s+visualState[\s\S]{0,220}/)
  assert.ok(match, 'visualState 定义必须存在')
  assert.match(
    match![0],
    /isOk \? 'green' : \(isScopeInfoOnly && !hasOtherRealError\) \? 'blue' : 'amber'/,
    'verdict 缺失时的本地推导必须与历史公式逐 token 一致',
  )
})

test('verdict prop 为可选且允许 null（旧调用点不传时零影响）', () => {
  assert.match(
    bannerSource,
    /verdict\?:\s*ScoringVerdict \| null/,
    'Props 必须声明 verdict?: ScoringVerdict | null',
  )
})

// ===== ⑤ 数据接线：三路回填 + 重提取清空 + 轮询透传（教训同款） =====

test('index.tsx 首载、手动刷新与轮询回调三处均经 transformScoringVerdict 回填', () => {
  const transformCalls = pageSource.match(/transformScoringVerdict\(/g) || []
  assert.ok(
    transformCalls.length >= 3,
    '首载 loadRequirements、refreshScoringTotalsReport 与轮询 onScoringVerdictLoaded ' +
      '三处必须都调用 transformScoringVerdict（：只接一条路会出现半成品回填）',
  )
  assert.match(pageSource, /scoring_verdict/, 'index.tsx 必须读取后端 scoring_verdict 字段')
  assert.match(
    pageSource,
    /verdict=\{scoringVerdict\}/,
    'ScoringTotalsBanner 必须接收 verdict prop',
  )
})

test('重新提取时清空旧 verdict（避免旧权威结论压住新一轮报告）', () => {
  const reextractIdx = pageSource.indexOf('reextractRequirements(')
  assert.ok(reextractIdx !== -1, '重新提取入口必须存在')
  assert.match(
    pageSource.slice(reextractIdx, reextractIdx + 600),
    /setScoringVerdict\(null\)/,
    '重提取后必须与 totals/ledger 报告一同清空 scoringVerdict',
  )
})

test('useRequirementExtractionPolling 轮询完成路径透传 scoring_verdict 原始值', () => {
  assert.match(
    pollingSource,
    /onScoringVerdictLoaded/,
    '轮询 hook 必须暴露 onScoringVerdictLoaded 回调',
  )
  assert.match(
    pollingSource,
    /scoring_verdict\?:\s*unknown/,
    '轮询完成回填必须读取 checkResponse.scoring_verdict 原始值（transform 由页面侧承担）',
  )
})
