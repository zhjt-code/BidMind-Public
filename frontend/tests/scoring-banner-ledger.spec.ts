/**
 * P5-1 账本对账（scoring_ledger_report）前端防御测试。
 *
 * 覆盖：
 * ① transformScoringLedgerReport 解析合法 payload；形状不合法（非 object /
 * status、basis_level 不在枚举内）返回 null
 * ② 低置信基准时源码路径不可能渲染"账本对账通过"
 * （ledgerReconciledOk 定义必须含 !ledgerReport.basisLowConfidence）
 * ③ 多方案信号 multiSchemeSignal 有展示路径
 * ④ 枚举中文映射表覆盖全部 4 个 basis_level 与 2 个 status（禁止英文枚举裸出）
 * ⑤ 组级缺口行渲染含声明/提取/差额三要素，且仅展示 |gap| > 0.01 的组
 * - 数据透传：index.tsx 首载 + 轮询完成回填双路都接入（教训）
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  formatLedgerGroupRow,
  formatSignedScore,
  ledgerBasisLevelLabel,
  ledgerStatusLabel,
  ledgerTotalGapHint,
  transformScoringLedgerReport,
  type ScoringLedgerBasisLevel,
  type ScoringLedgerStatus,
} from '../src/pages/requirementReviewTransforms.ts'

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

const ledgerCopySource = readFileSync(
  new URL('../src/pages/RequirementReview/scoringLedgerCopy.ts', import.meta.url),
  'utf8',
)

const bannerCopySource = readFileSync(
  new URL('../src/pages/RequirementReview/scoringBannerCopy.ts', import.meta.url),
  'utf8',
)

const validPayload = {
  status: 'review_required',
  basis_level: 'l1_explicit_group',
  basis_low_confidence: false,
  expected_total: 100.0,
  extracted_total: 96.0,
  total_gap: -4.0,
  group_rows: [{ label: '资信部分', declared: 100.0, extracted_sum: 96.0, gap: -4.0 }],
  unlabeled_sum: 0.0,
  multi_scheme_signal: '',
  insufficiency_count: 0,
  diagnostics: ['group_gap: 资信部分 -4.0'],
}

// ===== ① transform 解析合法 payload / 形状不合法返回 null =====

test('transformScoringLedgerReport 解析合法 review_required payload', () => {
  const report = transformScoringLedgerReport(validPayload)
  assert.notEqual(report, null, '合法 payload 不应返回 null')
  assert.equal(report?.status, 'review_required')
  assert.equal(report?.basisLevel, 'l1_explicit_group')
  assert.equal(report?.basisLowConfidence, false)
  assert.equal(report?.expectedTotal, 100)
  assert.equal(report?.extractedTotal, 96)
  assert.equal(report?.totalGap, -4)
  assert.equal(report?.groupRows.length, 1)
  assert.equal(report?.groupRows[0].label, '资信部分')
  assert.equal(report?.groupRows[0].declared, 100)
  assert.equal(report?.groupRows[0].extractedSum, 96)
  assert.equal(report?.groupRows[0].gap, -4)
  assert.equal(report?.unlabeledSum, 0)
  assert.equal(report?.multiSchemeSignal, '')
  assert.equal(report?.insufficiencyCount, 0)
  assert.deepEqual(report?.diagnostics, ['group_gap: 资信部分 -4.0'])
})

test('唯一余项守恒归集以结构化字段透传，并在横幅给出可见说明', () => {
  const report = transformScoringLedgerReport({
    ...validPayload,
    status: 'reconciled',
    inferred_group_label: '项目管理大纲部分',
    inferred_group_sum: 30,
    inferred_group_count: 9,
  })

  assert.notEqual(report, null)
  assert.equal(report!.inferredGroupLabel, '项目管理大纲部分')
  assert.equal(report!.inferredGroupSum, 30)
  assert.equal(report!.inferredGroupCount, 9)
  assert.match(bannerSource, /分值守恒核对/)
  assert.match(bannerSource, /inferredGroupLabel/)
})

test('transformScoringLedgerReport 解析合法 reconciled payload（null 差额字段）', () => {
  const report = transformScoringLedgerReport({
    status: 'reconciled',
    basis_level: 'l3_doc_total',
    basis_low_confidence: false,
    expected_total: null,
    extracted_total: 100,
    total_gap: null,
    group_rows: [],
    unlabeled_sum: 0,
    multi_scheme_signal: '',
    insufficiency_count: 0,
    diagnostics: [],
  })
  assert.notEqual(report, null)
  assert.equal(report?.status, 'reconciled')
  assert.equal(report?.expectedTotal, null, 'expected_total 为 null 时必须保持 null')
  assert.equal(report?.totalGap, null, 'total_gap 为 null 时必须保持 null')
})

test('transformScoringLedgerReport 形状不合法返回 null', () => {
  assert.equal(transformScoringLedgerReport(null), null)
  assert.equal(transformScoringLedgerReport(undefined), null)
  assert.equal(transformScoringLedgerReport('reconciled'), null)
  assert.equal(transformScoringLedgerReport(42), null)
  assert.equal(
    transformScoringLedgerReport({ ...validPayload, status: 'unknown_status' }),
    null,
    'status 不在枚举内必须返回 null',
  )
  assert.equal(
    transformScoringLedgerReport({ ...validPayload, status: undefined }),
    null,
    'status 缺失必须返回 null',
  )
  assert.equal(
    transformScoringLedgerReport({ ...validPayload, basis_level: 'l9_bogus' }),
    null,
    'basis_level 不在枚举内必须返回 null',
  )
  assert.equal(
    transformScoringLedgerReport({ ...validPayload, basis_level: undefined }),
    null,
    'basis_level 缺失必须返回 null',
  )
})

test('transformScoringLedgerReport 组行 null 安全：非对象/空 label 行丢弃，数值兜底 0', () => {
  const report = transformScoringLedgerReport({
    ...validPayload,
    group_rows: [
      null,
      'bad',
      { label: '  ', declared: 1, extracted_sum: 1, gap: 0 },
      { label: '技术部分', declared: '100', extracted_sum: 96, gap: -4 },
    ],
  })
  assert.notEqual(report, null)
  assert.equal(report?.groupRows.length, 1, '非对象/空 label 行必须被丢弃')
  assert.equal(report?.groupRows[0].label, '技术部分')
  assert.equal(report?.groupRows[0].declared, 0, '非 number 分值必须兜底为 0 而非 NaN')
})

test('transformScoringLedgerReport diagnostics 过滤非 string 且保序去重', () => {
  const report = transformScoringLedgerReport({
    ...validPayload,
    diagnostics: ['a', 1, 'b', 'a', null],
  })
  assert.deepEqual(report?.diagnostics, ['a', 'b'])
})

// ===== ② 低置信时源码路径不可能渲染"账本对账通过" =====

test("ScoringTotalsBanner 通过态渲染条件含 !basisLowConfidence（低置信禁止渲染'账本对账通过'）", () => {
  const okDefMatch = bannerSource.match(/const\s+ledgerReconciledOk\s*=\s*[^\n]+/)
  assert.ok(okDefMatch, 'ledgerReconciledOk 定义必须存在')
  assert.match(
    okDefMatch![0],
    /!ledgerReport\.basisLowConfidence/,
    'ledgerReconciledOk 必须要求 !ledgerReport.basisLowConfidence，' +
      '否则低置信基准会渲染成"账本对账通过"',
  )
  assert.match(
    okDefMatch![0],
    /status\s*===\s*'reconciled'/,
    "ledgerReconciledOk 必须要求 status === 'reconciled'",
  )
})

test('账本通过态不得独立渲染（与主结论重复）', () => {
  // 行为变更(非文案调整): 通过时 ledgerReconciledOk 是 isOk 的子条件, 主结论必然已用
  // "已成功提取…并完成分值校验"表达; 再渲染"要求100分/已提取100分/N个卷全部对上"
  // 属同义重复(用户实测反馈"上下两个信息完全一样")。原断言"必须有且仅有一处渲染路径"
  // 的防御目标(避免低置信被误渲染为通过)由 ledgerReconciledOk 定义本身守卫, 见上一测试。
  assert.doesNotMatch(bannerSource, /账本对账通过/)
  assert.doesNotMatch(bannerSource, /已与招标文件核对一致/)
  assert.doesNotMatch(
    bannerSource,
    /ledgerReconciledOk && ledgerReport && \(/,
    'ledgerReconciledOk 不得再作为独立渲染块的门控',
  )
  assert.match(
    bannerSource,
    /ledgerReconciledOk/,
    'ledgerReconciledOk 变量必须保留，用于 ledgerNeedsReview 判定',
  )
})

test('ScoringTotalsBanner 复核态由 ledgerNeedsReview 驱动且早退条件不静默账本报告', () => {
  assert.match(bannerSource, /ledgerNeedsReview/, '必须定义 ledgerNeedsReview 变量')
  assert.match(
    bannerSource,
    /!ledgerReport\s*\)/,
    '早退条件必须含 !ledgerReport，否则仅有账本报告时 Banner 会被静默',
  )
  // 去术语化: 标题不再是"账本对账需复核"这一笼统术语, 改为按真实原因
  // 分支的用户语言(6 类原因各有独立文案, 见 ledgerReviewHeadline)。
  assert.match(
    bannerSource,
    /ledgerReviewHeadline\(/,
    '复核态标题必须经 ledgerReviewHeadline 按真实原因取文案',
  )
  assert.doesNotMatch(bannerSource, /账本对账需复核/, '内部术语"账本对账"不得面向用户')
  // Task7: isOk 改用 effectiveLedgerNeedsReview（多包模式下 globalComparisonAllowed=false
  // 时 effectiveLedgerNeedsReview=false，防止 top-level L4 把全通过 scope 横幅拉回 amber）。
  // 防御目标不变：当 globalComparisonAllowed=true 时，effectiveLedgerNeedsReview = ledgerNeedsReview，
  // 账本需复核时横幅仍不整体绿色通过。
  const isOkMatch = bannerSource.match(/const\s+isOk\s*=\s*[^\n]+/)
  assert.ok(isOkMatch, 'isOk 定义必须存在')
  assert.match(
    isOkMatch![0],
    /!effectiveLedgerNeedsReview/,
    'isOk 必须含 !effectiveLedgerNeedsReview；global=true 时等价于 !ledgerNeedsReview，' +
      '账本需复核时不得整体绿色通过（Task7 防御目标不变）',
  )
})

test('复核态展示低置信标注（去术语化后仍须由 basisLowConfidence 驱动）', () => {
  assert.match(
    bannerSource,
    /basisLowConfidence\s*&&/,
    '低置信标注必须由 basisLowConfidence 条件驱动',
  )
  assert.match(
    bannerSource,
    /SCORING_MANUAL_CHECK_NOTICE/,
    '低置信补充提示必须收敛为统一人工核实短句',
  )
  assert.doesNotMatch(bannerSource, /低置信（推导/, '术语文案不得再面向用户')
  // "推算"暗示估计成分，而实际是确定性求和；面向用户文案一律不得再用
  assert.doesNotMatch(
    bannerSource,
    /系统推算|按评分卷推算/,
    '面向用户文案不得用"推算"描述确定性求和',
  )
})

// ===== ③ 多方案信号有展示路径 =====

test('ScoringTotalsBanner 多方案明细经 multiSchemeDetail 收敛（去重去术语）', () => {
  // 行为变更: 文案构造下沉到 scoringLedgerCopy，横幅只渲染其返回值。原断言要求
  // 横幅内联 `multiSchemeSignal !== ''` 与"检测到多套评分方案"，那正是与标题
  // 重复、且回显后端内部判据串的写法（项目2476 同屏两句"检测到多套评分方案"）。
  assert.match(bannerSource, /multiSchemeDetail\(/, '多方案明细必须经 transform 收敛')
  assert.match(bannerSource, /\{multiSchemeText\}/, '横幅只渲染收敛后的文案')
  assert.doesNotMatch(
    bannerSource,
    /检测到多套评分方案：\{/,
    '不得把后端原始判据串拼进用户文案',
  )
  assert.match(
    ledgerCopySource,
    /headlineReason === 'multi_scheme'/,
    '标题已表述多方案时明细必须让位（去重铁律）',
  )
})

// ===== ④ 枚举中文映射覆盖全部 basis_level 与 status =====

test('ledgerBasisLevelLabel 覆盖全部 4 个 basis_level 且输出中文（改为用户语言）', () => {
  const expected: Record<ScoringLedgerBasisLevel, string> = {
    l1_explicit_group: '招标文件逐卷写明的分值',
    l2_derived_group: '由已提取评分项归集（未经原文核对）',
    l3_doc_total: '招标文件写明的总分',
    l4_none: '未在招标文件中找到分值说明',
  }
  for (const [level, label] of Object.entries(expected)) {
    const actual = ledgerBasisLevelLabel(level as ScoringLedgerBasisLevel)
    assert.equal(actual, label, `basis_level=${level} 的中文映射不符`)
    assert.doesNotMatch(actual, /[a-z_]/i, `basis_level=${level} 的映射不得裸出英文枚举`)
  }
})

test('ledgerStatusLabel 覆盖全部 2 个 status 且输出中文', () => {
  const expected: Record<ScoringLedgerStatus, string> = {
    reconciled: '对账通过',
    review_required: '需复核',
  }
  for (const [status, label] of Object.entries(expected)) {
    const actual = ledgerStatusLabel(status as ScoringLedgerStatus)
    assert.equal(actual, label, `status=${status} 的中文映射不符`)
    assert.doesNotMatch(actual, /[a-z_]/i, `status=${status} 的映射不得裸出英文枚举`)
  }
})

test('基准层级不再向用户展示，且源码不得裸出账本英文枚举', () => {
  // 行为变更: basisLevel 是账本内部分级(l1/l2/l3/l4), 对用户零信息量, 已从展示层移除;
  // 其中文映射 ledgerBasisLevelLabel 仍保留并受上一测试守卫, 供后续诊断用途复用。
  assert.doesNotMatch(
    bannerSource,
    /ledgerBasisLevelLabel\(/,
    '基准分级属实现细节，不得渲染给用户',
  )
  // 原防御目标（禁止裸出英文枚举）仍然有效且更为本质
  assert.doesNotMatch(
    bannerSource,
    /['"`]l1_explicit_group['"`]|['"`]review_required['"`]/,
    'Banner 源码不得直接比较/渲染账本英文枚举字面量（应由 transform 层收敛）',
  )
})

// ===== ⑤ 组行渲染含声明/提取/差额三要素 =====

test('formatLedgerGroupRow 输出含要求/已提取/差额三要素且方向明确（去术语化）', () => {
  const negative = formatLedgerGroupRow({
    label: '资信部分',
    declared: 100,
    extractedSum: 96,
    gap: -4,
  })
  assert.equal(negative, '资信部分：要求 100 分，已提取 96 分（少 4 分）')
  const positive = formatLedgerGroupRow({
    label: '技术部分',
    declared: 50,
    extractedSum: 56,
    gap: 6,
  })
  assert.equal(positive, '技术部分：要求 50 分，已提取 56 分（多 6 分）')
})

test('formatSignedScore 正数补 +，负数保留 -', () => {
  assert.equal(formatSignedScore(6), '+6')
  assert.equal(formatSignedScore(-4), '-4')
  assert.equal(formatSignedScore(0), '0')
})

test('ScoringTotalsBanner 组行经 formatLedgerGroupRow 渲染且仅展示 |gap| > 0.01 的组', () => {
  assert.match(
    bannerSource,
    /formatLedgerGroupRow\(/,
    '组级缺口行必须经 formatLedgerGroupRow 渲染（三要素文案收敛在 transform 层）',
  )
  assert.match(
    bannerSource,
    /Math\.abs\(row\.gap\)\s*>\s*0\.01/,
    '组行必须按 |gap| > 0.01 过滤，闭合组不得重复罗列',
  )
})

// ===== 总差额方向分支文案（教训） =====

test('ledgerTotalGapHint 负差额=可能漏提，正差额=可能重复或多方案（方向性不得共用文案）', () => {
  const negative = ledgerTotalGapHint(-4)
  assert.match(negative, /少 4 分/, '负差额必须给出方向与数量')
  assert.match(negative, /没提取到/, '负差额必须指向漏提')
  assert.doesNotMatch(negative, /重复/, '负差额不得提示重复提取')
  const positive = ledgerTotalGapHint(6)
  assert.match(positive, /多 6 分/, '正差额必须给出方向与数量')
  assert.match(positive, /重复|多套评分方案/, '正差额必须提示可能重复或多套方案')
  assert.doesNotMatch(positive, /没提取到/, '正差额不得提示漏提')
})

// ===== 未归组分值提示 =====

test('ScoringTotalsBanner 未归卷提示改为构成展示（其它提取/已提分数项）', () => {
  // 行为变更: 用户无需先理解"未归卷"诊断术语；横幅展示层把这部分分值
  // 合成可核对的构成项，旧的子集提示函数可保留给历史单测，但不再进入 Banner 渲染。
  assert.match(
    bannerSource,
    /buildScoringCompositionText\(/,
    '未归卷构成必须经 scoringBannerCopy 收敛',
  )
  assert.doesNotMatch(
    bannerSource,
    /unlabeledScoreNotice\(/,
    'Banner 不再渲染“尚未识别所属评分卷”诊断句',
  )
  assert.match(bannerCopySource, /其它提取/, '部分未归卷应合成为“其它提取”')
  assert.match(bannerCopySource, /已提分数项/, '完全未归卷应合成为“已提分数项”')
  assert.doesNotMatch(bannerSource, /另有 \{formatScore\(ledgerReport\.unlabeledSum\)/)
  assert.match(ledgerCopySource, /其它提取/, '历史 helper 同步收敛为构成文案')
})

// ===== 诊断信息分流 =====

test('账本 diagnostics 不进展示层，report.diagnostics 保留在折叠区', () => {
  // 行为变更: 账本 diagnostics 是内部术语(如"基准层级l2_derived_group不足以支撑通过
  // 判定"), 含英文枚举、对用户零信息量, 已移出展示层; 而 report.diagnostics 含
  // "已识别构成：价格 30.0 分（第34页）"这类页码定位, 必须保留以供核对原文。
  assert.doesNotMatch(
    bannerSource,
    /ledgerReport\.diagnostics/,
    '账本 diagnostics 属实现细节，不得渲染给用户',
  )
  const detailsIdx = bannerSource.indexOf('分值核对明细')
  assert.ok(detailsIdx !== -1, '折叠区必须存在且标签为用户语言')
  const reportDiagIdx = bannerSource.indexOf('report.diagnostics.map', detailsIdx)
  assert.ok(reportDiagIdx > detailsIdx, 'report.diagnostics 必须渲染在折叠区内部')
})

// ===== 信息架构：层间不得重复 =====

test('主结论图标随语义切换——通过用对号，需处理用感叹号', () => {
  assert.match(
    bannerSource,
    /isOk\s*\n?\s*\?\s*'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z'/,
    '通过态必须使用 check-circle 路径（绿底感叹号自相矛盾）',
  )
  assert.match(
    bannerSource,
    /'M12 9v2m0 4h\.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'/,
    '需处理态必须使用 alert-circle 路径',
  )
})

test('提醒区不得再放圆形图标（与主结论图标视觉重复）', () => {
  assert.doesNotMatch(bannerSource, /from 'lucide-react'/, '提醒区已改为纯文字+蓝色底')
  assert.doesNotMatch(bannerSource, /<Info\s/, '不得渲染圆形 Info 图标')
  assert.match(bannerSource, /bg-blue-50\/50/, '提醒区仍须以蓝色底与琥珀警告区分')
})

// ===== 数据透传：首载 + 轮询完成回填双路（教训） =====

test('RequirementReview/index.tsx 首载、手动刷新与轮询回调三处均透传 scoring_ledger_report', () => {
  const transformCalls = pageSource.match(/transformScoringLedgerReport\(/g) || []
  assert.ok(
    transformCalls.length >= 3,
    '首载 loadRequirements、refreshScoringTotalsReport 与轮询 onScoringLedgerReportLoaded ' +
      '三处必须都调用 transformScoringLedgerReport（：只接一条路会出现半成品回填）',
  )
  assert.match(
    pageSource,
    /scoring_ledger_report/,
    'index.tsx 必须读取后端 scoring_ledger_report 字段',
  )
  assert.match(
    pageSource,
    /ledgerReport=\{scoringLedgerReport\}/,
    'ScoringTotalsBanner 必须接收 ledgerReport prop',
  )
})

test('useRequirementExtractionPolling 轮询完成路径透传 scoring_ledger_report', () => {
  assert.match(
    pollingSource,
    /onScoringLedgerReportLoaded/,
    '轮询 hook 必须暴露 onScoringLedgerReportLoaded 回调',
  )
  assert.match(
    pollingSource,
    /scoring_ledger_report/,
    '轮询完成回填必须读取 scoring_ledger_report 字段',
  )
})

test('ScoringTotalsBanner 通过态也展示疑似履约考核建议排除提示', () => {
  assert.match(bannerSource, /suspectedAssessmentCount/)
  assert.match(bannerSource, /buildSuspectedAssessmentNotice/)
})
