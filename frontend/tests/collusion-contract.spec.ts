import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { mapCollusionResultsRaw } from '../src/services/collusionResultsMapper.ts'
import { transformCollusionPairResultRaw } from '../src/services/collusionTransforms.ts'
import { buildPairRefReplacer, sortPairsByRisk } from '../src/pages/collusionResult/types.ts'
import { PAGE_ONLY_EVIDENCE_NOTICE, UNVERIFIABLE_FINDING_NOTICE } from '../src/pages/collusionResult/evidenceDisplay.ts'
import { shouldAutoStartCollusionAnalysis } from '../src/pages/collusionUploadState.ts'

test('transformPairResult 应映射新合同字段与 dimension_certainties', () => {
  const pair = transformCollusionPairResultRaw({
    pair_id: '11:22',
    doc_a_id: 11,
    doc_b_id: 22,
    doc_a_name: 'A.pdf',
    doc_b_name: 'B.pdf',
    overall_risk: 'inconclusive',
    findings: [],
    analysis_mode: 'failed',
    review_state: 'failed',
    analysis_state: 'failed',
    decision_state: 'failed',
    quality_flags: ['分析失败，当前仅保留占位结果'],
    consistency_flags: [],
    risk_explanation: '分析失败，需人工复核',
    dimension_certainties: { d3_text: 0.18 },
  })

  assert.equal(pair.pairId, '11:22')
  assert.equal(pair.reviewState, 'failed')
  assert.equal(pair.analysisState, 'failed')
  assert.equal(pair.decisionState, 'failed')
  assert.deepEqual(pair.qualityFlags, ['分析失败，当前仅保留占位结果'])
  assert.deepEqual(pair.dimensionCertainties, { d3_text: 0.18 })
})

test('结果接口字段应通过 service 映射项目级业务结论语义', () => {
  const mapped = mapCollusionResultsRaw({
    project_id: 1,
    overall_risk: 'inconclusive',
    project_decision: 'partial_review_needed',
    project_risk_level: 'none',
    decision_confidence: 'medium',
    review_required: true,
    decision_basis: 'pairs',
    decision_engine_version: 2,
    overall_risk_basis: 'groups',
    risk_distribution_basis: 'groups',
    risk_consistency_status: 'diverged',
    risk_consistency_note: '顶部整体风险按群组风险口径展示',
    coverage_stats: {
      review_required_pairs: 2,
      clear_pairs: 1,
      suspicious_pairs: 0,
    },
    pair_results: [],
    groups: [],
    global_findings: [],
    suspected_groups: [],
    document_labels: [],
    similarity_matrix: [],
  })

  assert.equal(mapped.projectDecision, 'partial_review_needed')
  assert.equal(mapped.projectRiskLevel, 'none')
  assert.equal(mapped.decisionConfidence, 'medium')
  assert.equal(mapped.reviewRequired, true)
  assert.equal(mapped.decisionEngineVersion, 2)
  assert.equal(mapped.overallRiskBasis, 'groups')
  assert.equal(mapped.riskDistributionBasis, 'groups')
  assert.equal(mapped.riskConsistencyStatus, 'diverged')
  assert.equal(mapped.coverageStats.reviewRequiredPairs, 2)
  assert.equal(mapped.coverageStats.clearPairs, 1)
  assert.ok(String(mapped.riskConsistencyNote).includes('群组'))
})

test('sortPairsByRisk 应兼容大小写风险值', () => {
  const sorted = sortPairsByRisk([
    {
      docAId: 1, docBId: 2, docAName: 'A.pdf', docBName: 'B.pdf',
      overallRisk: 'LOW', findings: [], analysisMode: 'algorithm_only',
      analysisState: 'completed', decisionState: 'decidable', qualityFlags: [],
      consistencyFlags: [], riskExplanation: '',
    },
    {
      docAId: 3, docBId: 4, docAName: 'C.pdf', docBName: 'D.pdf',
      overallRisk: 'high', findings: [], analysisMode: 'full',
      analysisState: 'completed', decisionState: 'decidable', qualityFlags: [],
      consistencyFlags: [], riskExplanation: '',
    },
  ])

  assert.equal(sorted[0].overallRisk, 'high')
  assert.equal(sorted[1].overallRisk, 'LOW')
})

const pairStub = (overrides: Record<string, unknown> = {}) => ({
  pairId: '5:9',
  docAId: 5,
  docBId: 9,
  docAName: '甲公司.pdf',
  docBName: '乙公司.pdf',
  overallRisk: 'medium',
  findings: [],
  analysisMode: 'full',
  analysisState: 'completed',
  decisionState: 'decidable',
  qualityFlags: [],
  consistencyFlags: [],
  riskExplanation: '',
  ...overrides,
})

test('buildPairRefReplacer 优先替换结构化 pair_id 引用', () => {
  const replace = buildPairRefReplacer([pairStub()])

  assert.equal(
    replace('请重点关注 5:9 以及后续人工复核意见。'),
    '请重点关注 【甲公司×乙公司】 以及后续人工复核意见。',
  )
})

test('buildPairRefReplacer 将文件对中的文档身份编号替换为上传文件名', () => {
  const replace = buildPairRefReplacer([
    pairStub({
      pairId: '2517:2518',
      docAId: 2517,
      docBId: 2518,
      docAName: '甲公司投标文件.pdf',
      docBName: '乙公司技术方案.pdf',
    }),
  ])
  const text = replace('针对文件对2517与2518的分析显示，元数据时间戳接近。')
  assert.equal(text.includes('引用无效'), false)
  assert.equal(text.includes('2517'), false)
  assert.equal(text.includes('2518'), false)
  assert.ok(text.includes('甲公司投标文件'))
  assert.ok(text.includes('乙公司技术方案'))
})

test('buildPairRefReplacer 将文档身份编号替换为上传文件名（同形态不同实体）', () => {
  const replace = buildPairRefReplacer([
    pairStub({
      pairId: '101:205',
      docAId: 101,
      docBId: 205,
      docAName: '华南路桥施工组织设计.pdf',
      docBName: '华北市政投标文件.pdf',
    }),
  ])
  const text = replace('针对文档101与205的分析显示，doc_id=101 已复核。')
  assert.equal(text.includes('101'), false)
  assert.equal(text.includes('205'), false)
  assert.ok(text.includes('华南路桥施工组织设计'))
  assert.ok(text.includes('华北市政投标文件'))
})

test('shouldAutoStartCollusionAnalysis 仅在全部解析完成且项目未进入终态时触发', () => {
  // 修复后核心场景：文档就绪 + projectStatus 非终态 → 自动启动
  // 必须能覆盖"用户离开后再返回"的情况，因此判定不依赖任何会话级哨兵变量
  assert.equal(
    shouldAutoStartCollusionAnalysis({
      canStartAnalysis: true,
      isSomethingLoading: false,
      isUploading: false,
      projectStatus: 'uploading',
    }),
    true,
    '文档就绪且 projectStatus=uploading 时应自动启动',
  )

  assert.equal(
    shouldAutoStartCollusionAnalysis({
      canStartAnalysis: true,
      isSomethingLoading: false,
      isUploading: false,
      projectStatus: 'parsing',
    }),
    true,
    'parsing 状态下文档已 parsed=1 时应自动启动',
  )

  // 终态保护：避免重复启动已经/已经完成的分析
  assert.equal(
    shouldAutoStartCollusionAnalysis({
      canStartAnalysis: true,
      isSomethingLoading: false,
      isUploading: false,
      projectStatus: 'reviewing',
    }),
    false,
    'reviewing 状态下不应重复启动分析',
  )

  assert.equal(
    shouldAutoStartCollusionAnalysis({
      canStartAnalysis: true,
      isSomethingLoading: false,
      isUploading: false,
      projectStatus: 'completed',
    }),
    false,
    'completed 状态下不应重新启动分析',
  )

  assert.equal(
    shouldAutoStartCollusionAnalysis({
      canStartAnalysis: true,
      isSomethingLoading: false,
      isUploading: false,
      projectStatus: 'failed',
    }),
    false,
    'failed 状态下应停留让用户决定是否重试',
  )

  // 短路保护：基础前提不满足时一律不启动
  assert.equal(
    shouldAutoStartCollusionAnalysis({
      canStartAnalysis: false,
      isSomethingLoading: false,
      isUploading: false,
      projectStatus: 'uploading',
    }),
    false,
    'canStartAnalysis=false 时应短路',
  )

  assert.equal(
    shouldAutoStartCollusionAnalysis({
      canStartAnalysis: true,
      isSomethingLoading: true,
      isUploading: false,
      projectStatus: 'uploading',
    }),
    false,
    '存在文档处理中时应短路',
  )

  assert.equal(
    shouldAutoStartCollusionAnalysis({
      canStartAnalysis: true,
      isSomethingLoading: false,
      isUploading: true,
      projectStatus: 'uploading',
    }),
    false,
    '上传中应短路',
  )

  // 在途保护：handleStartAnalysis 已经发起后，避免 StrictMode/effect 依赖变动
  // 触发的二次并发 POST /analyze 命中后端"已在进行中"分支
  assert.equal(
    shouldAutoStartCollusionAnalysis({
      canStartAnalysis: true,
      isSomethingLoading: false,
      isUploading: false,
      projectStatus: 'uploading',
      isStartingAnalysis: true,
    }),
    false,
    'isStartingAnalysis=true 时应短路，避免并发重复启动',
  )

  // 兼容性保护：未传 isStartingAnalysis（既有调用）默认按 false 处理
  assert.equal(
    shouldAutoStartCollusionAnalysis({
      canStartAnalysis: true,
      isSomethingLoading: false,
      isUploading: false,
      projectStatus: 'uploading',
      isStartingAnalysis: false,
    }),
    true,
    'isStartingAnalysis=false 时仍应正常自动启动',
  )

  // 计费准入失败后项目仍非终态，必须截断自动启动，避免 toast 风暴
  assert.equal(
    shouldAutoStartCollusionAnalysis({
      canStartAnalysis: true,
      isSomethingLoading: false,
      isUploading: false,
      projectStatus: 'uploading',
      isStartingAnalysis: false,
      analysisBlockedByBalance: true,
    }),
    false,
    '余额不足阻断时应停止自动启动',
  )

  assert.equal(
    shouldAutoStartCollusionAnalysis({
      canStartAnalysis: true,
      isSomethingLoading: false,
      isUploading: false,
      projectStatus: 'uploading',
      isStartingAnalysis: false,
      analysisBlockedByBalance: false,
    }),
    true,
    '未阻断时仍应自动启动',
  )
})

// P2-3: attention_items 映射
test('transformPairResult 应映射 attention_items 到 attentionItems (camelCase)', () => {
  const pair = transformCollusionPairResultRaw({
    pair_id: '1:2',
    doc_a_id: 1,
    doc_b_id: 2,
    doc_a_name: 'A.pdf',
    doc_b_name: 'B.pdf',
    overall_risk: 'low',
    findings: [],
    analysis_mode: 'full',
    analysis_state: 'completed',
    decision_state: 'decidable',
    quality_flags: [],
    consistency_flags: [],
    risk_explanation: '',
    attention_items: [
      {
        category: 'business_relationship',
        dimension: 'D5',
        summary: '检测到业务往来关系',
        detail: '两家投标企业存在甲乙方合作关系',
        source_reason: '业务往来不构成串标证据但值得关注',
      },
      {
        category: 'unverified_quote',
        dimension: 'D3',
        summary: '引文有实质内容但页码无法验证',
      },
    ],
  })

  assert.ok(Array.isArray(pair.attentionItems))
  assert.equal(pair.attentionItems.length, 2)

  assert.equal(pair.attentionItems[0].category, 'business_relationship')
  assert.equal(pair.attentionItems[0].dimension, 'D5')
  assert.equal(pair.attentionItems[0].summary, '检测到业务往来关系')
  assert.equal(pair.attentionItems[0].detail, '两家投标企业存在甲乙方合作关系')
  assert.equal(pair.attentionItems[0].sourceReason, '业务往来不构成串标证据但值得关注')

  // 可选字段缺失时应不报错
  assert.equal(pair.attentionItems[1].category, 'unverified_quote')
  assert.equal(pair.attentionItems[1].detail, undefined)
  assert.equal(pair.attentionItems[1].sourceReason, undefined)
})

test('串标 page-only 和无引用证据应有明确兜底提示文案', () => {
  assert.match(PAGE_ONLY_EVIDENCE_NOTICE, /原文摘录未能自动定位/)
  assert.match(PAGE_ONLY_EVIDENCE_NOTICE, /点击页码打开 PDF 核验/)
  assert.match(UNVERIFIABLE_FINDING_NOTICE, /未提供可复核原文引用/)
  assert.match(UNVERIFIABLE_FINDING_NOTICE, /不能单独作为正式证据/)
})

test('PairDetail 用户可见 flags 与风险说明应有展示层清洗兜底', () => {
  const source = readFileSync(new URL('../src/pages/collusionResult/PairDetail.tsx', import.meta.url), 'utf8')

  assert.match(source, /sanitizeUserFacingText/)
  assert.match(source, /cleanedQualityFlags/)
  assert.match(source, /cleanedConsistencyFlags/)
  assert.match(source, /cleanedRiskExplanation/)
})

test('transformPairResult attention_items 缺失时应返回空数组', () => {
  const pair = transformCollusionPairResultRaw({
    pair_id: '1:2',
    doc_a_id: 1,
    doc_b_id: 2,
    doc_a_name: 'A.pdf',
    doc_b_name: 'B.pdf',
    overall_risk: 'none',
    findings: [],
    analysis_mode: 'algorithm_only',
    analysis_state: 'completed',
    decision_state: 'decidable',
    quality_flags: [],
    consistency_flags: [],
    risk_explanation: '',
  })

  assert.ok(Array.isArray(pair.attentionItems))
  assert.equal(pair.attentionItems.length, 0)
})
