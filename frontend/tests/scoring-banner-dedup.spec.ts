/**
 * 防御测试：评分核对提示条的层间去重（去重铁律， 同族）。
 *
 * 真实症状（项目2447 现场）：同一屏同时出现
 * legacy 报告："评分项分值与评分表不一致，请人工核对" + "请对照招标文件的评分表，补齐或修正后再开始审查。"
 * 账本区块："" + "详细评审商务：要求 20 分，已提取 11 分（少 9 分）"
 * 两段是同一判定的两次陈述，且账本那份更具体，legacy 那份纯占版面。
 *
 * 锁定的不变量：
 * 1. 账本区块渲染时，legacy 的判定句与泛化建议句不得渲染（去重）
 * 2. 账本一条差额行都没列时，legacy 的卷缺口明细必须保留（两轴分歧不得静默）
 * 3. 账本自洽且组行完整覆盖提取总分时，legacy 旧轴复核结论整体让位
 * （否则 2471 会出现"三卷都对上"但仍报"分值不一致"）
 * 4. 账本判定原因已是"缺少评审标准"时，带项数的 missingRuleHint 不重复
 * 5. 两条 legacy 文案本体必须仍在源码中——无账本报告时它们是唯一的判定载体
 * 6. 写标页同类横幅：只列有差额的卷、诊断收折叠、不重复同义"请核对"指令
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const bannerSource = readFileSync(
  new URL('../src/pages/RequirementReview/components/ScoringTotalsBanner.tsx', import.meta.url),
  'utf8',
)
const writingBannerSource = readFileSync(
  new URL('../src/pages/writing/WritingScoringTotalsBanner.tsx', import.meta.url),
  'utf8',
)

/** 取某个 const 定义起始处的固定长度窗口（不依赖 CRLF/LF 行尾风格）。 */
function gateBody(source: string, name: string): string {
  const idx = source.search(new RegExp(`const\\s+${name}\\s*=`))
  assert.ok(idx !== -1, `门禁变量 ${name} 必须存在`)
  return source.slice(idx, idx + 400)
}

// ===== 1. 账本区块渲染时 legacy 判定与建议让位 =====

test('legacy 判定句由 legacyVerdictVisible 驱动，且该门禁要求账本未接管判定', () => {
  assert.match(
    gateBody(bannerSource, 'legacyVerdictVisible'),
    /reportNeedsReview\s*&&\s*!legacyReviewSuperseded\s*&&\s*!ledgerNeedsReview/,
    'legacy 判定仅在账本未给出判定时渲染',
  )

  const titleIdx = bannerSource.indexOf('评分项分值与评分表不一致，请人工核对')
  assert.ok(titleIdx !== -1, 'legacy 判定文案必须仍在源码中（无账本时的唯一载体）')
  assert.match(
    bannerSource.slice(Math.max(0, titleIdx - 300), titleIdx),
    /legacyVerdictVisible/,
    'legacy 判定句必须由 legacyVerdictVisible 门禁驱动',
  )
})

test('legacy 泛化建议句同样由 legacyVerdictVisible 驱动，不得与账本判定同屏', () => {
  const hintIdx = bannerSource.indexOf('请对照招标文件的评分表，补齐或修正后再开始审查。')
  assert.ok(hintIdx !== -1, 'legacy 建议文案必须仍在源码中')
  assert.match(
    bannerSource.slice(Math.max(0, hintIdx - 300), hintIdx),
    /legacyVerdictVisible/,
    'legacy 建议句必须由 legacyVerdictVisible 门禁驱动',
  )
})

// ===== 2. 账本已完整自洽时 legacy 旧轴让位 =====

test('账本组行完整覆盖且无账本错误时，legacy 复核结论不再参与红黄状态', () => {
  assert.match(
    gateBody(bannerSource, 'legacyGroupShares'),
    /ledgerGroupShares\(ledgerReport\)/,
    '必须复用原文组构成的完整覆盖判据，不能另写一套覆盖逻辑',
  )
  assert.match(
    gateBody(bannerSource, 'legacyReviewSuperseded'),
    /reportNeedsReview\s*&&\s*!ledgerNeedsReview\s*&&[\s\S]*legacyGroupShares\.length > 0/,
    '账本自洽且组行完整覆盖时，legacy 旧轴复核结论应整体让位',
  )
  assert.match(
    gateBody(bannerSource, 'effectiveReportNeedsReview'),
    /reportNeedsReview\s*&&\s*!legacyReviewSuperseded/,
    'isOk 必须消费让位后的 effectiveReportNeedsReview，避免正确账本被 legacy 拉回 amber',
  )
  assert.match(
    gateBody(bannerSource, 'legacyDiagnosticsVisible'),
    /report\.diagnostics\.length > 0\s*&&\s*!legacyReviewSuperseded/,
    '让位时 legacy diagnostics 不得继续展示"分值缺失"等矛盾明细',
  )
})

test('结构复核标记禁止 legacyReviewSuperseded 隐藏说明与 diagnostics', () => {
  assert.match(
    gateBody(bannerSource, 'legacyReviewSuperseded'),
    /!report\?\.structureReviewRequired/,
    '结构缺失必须阻断 legacy 让位，保留后端 amber 对应的复核说明与 diagnostics',
  )
})

test('并行百分制卷的L2自洽可压制旧默认100告警，但不得隐藏真实规则或分值缺口', () => {
  const softGate = gateBody(bannerSource, 'legacySoftBasisSuperseded')
  assert.match(softGate, /ledgerSoftBasisOnly/, '并行百分制卷需允许L2自洽接管旧轴')
  assert.match(softGate, /report\.missingRuleCount\s*===\s*0/, '真实规则缺口不得被L2自洽隐藏')
  assert.match(softGate, /report\.missingScoreCount\s*===\s*0/, '真实分值缺口不得被L2自洽隐藏')
  assert.match(softGate, /problemVolumes\.length\s*===\s*0/, '真实评分卷缺口不得被L2自洽隐藏')
  assert.match(
    gateBody(bannerSource, 'legacyReviewSuperseded'),
    /legacyGroupShares\.length > 0\s*\|\|\s*legacySoftBasisSuperseded/,
    '完整组行或并行百分制L2自洽任一成立时，旧轴可让位',
  )
})

// ===== 3. 两轴分歧时不得静默 =====

test('账本无差额行而 legacy 检出卷缺口时必须保留 legacy 明细', () => {
  assert.match(
    gateBody(bannerSource, 'legacyVolumeGapsVisible'),
    /!legacyReviewSuperseded\s*&&[\s\S]*\(!ledgerNeedsReview\s*\|\|\s*ledgerGapRows\.length === 0\)/,
    '账本一条差额行都没列时必须回退展示 legacy 卷缺口，否则两轴分歧会静默丢失',
  )
  assert.match(
    gateBody(bannerSource, 'legacyVolumeGapsVisible'),
    /problemVolumes\.length\s*>\s*0/,
    '仅在 legacy 确实检出问题卷时才渲染（不渲染闭合卷）',
  )
})

// ===== 4. 评审标准缺失提示不与账本同因重复 =====

test('missingRuleHint 在账本判定原因已是缺少评审标准时不重复', () => {
  assert.match(
    gateBody(bannerSource, 'missingRuleHintVisible'),
    /ledgerReviewReason !== 'insufficiency'/,
    '账本已以"缺少评审标准"作判定标题时不再重复同义提示',
  )
  const hintIdx = bannerSource.indexOf('{missingRuleHint}')
  assert.ok(hintIdx !== -1, 'missingRuleHint 渲染点必须存在')
  assert.match(
    bannerSource.slice(Math.max(0, hintIdx - 200), hintIdx),
    /missingRuleHintVisible/,
    'missingRuleHint 必须由 missingRuleHintVisible 门禁驱动',
  )
})

// ===== 5. 去重不得退化为"直接删文案" =====

test('legacy 两条文案本体保留（无账本报告时仍需可见判定）', () => {
  assert.match(bannerSource, /评分项分值与评分表不一致，请人工核对/)
  assert.match(bannerSource, /评分项规则信息待完善/)
  assert.match(bannerSource, /请对照招标文件的评分表，补齐或修正后再开始审查。/)
  assert.doesNotMatch(
    gateBody(bannerSource, 'legacyVerdictVisible'),
    /false/,
    '门禁不得被写死为常量 false（等于变相删除唯一判定载体）',
  )
})

// ===== 6. 写标页同类横幅精简 =====

test('写标评分横幅只列有差额的卷', () => {
  assert.match(
    writingBannerSource,
    /const problemVolumes = Object\.entries\(report\.volumeChecks\)\.filter\(\(\[, c\]\) => !c\.within\)/,
    '必须过滤掉闭合卷（闭合卷只会渲染"正常"，零信息量）',
  )
  assert.doesNotMatch(
    writingBannerSource,
    /visibleVolumes/,
    '不得保留未过滤的全量卷列表变量',
  )
})

test('写标评分横幅诊断收进折叠区', () => {
  assert.match(writingBannerSource, /<details/, '诊断必须可折叠')
  assert.match(writingBannerSource, /分值核对明细/, '折叠标题与投标审查确认页一致')
})

test('写标评分横幅用分值异常口径提示后续响应风险', () => {
  assert.match(writingBannerSource, /评分项分值需核对，请确认识别结果/, '判定标题保留')
  assert.match(writingBannerSource, /分值异常会影响后续章节响应重点/, '写标特有后果必须保留')
  assert.doesNotMatch(
    writingBannerSource,
    /请对照招标文件的评分表核对评分项与分值/,
    '标题已说"请核对"，不得再重复同义泛化指令',
  )
})
