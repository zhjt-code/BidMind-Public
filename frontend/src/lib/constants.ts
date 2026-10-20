// 投标信封卷角色标签(商务标/技术标), 单一数据源在 lib/bidEnvelope
// 带 .ts 扩展: node --experimental-strip-types 测试链会加载本文件(见 tests/*.spec.ts)
export { bidPartLabels, bidPartLabel } from './bidEnvelope.ts'

export const categoryLabels: Record<string, string> = {
  QUAL: '资质要求',
  TECH: '技术要求',
  COMM: '商务要求',
  PERF: '业绩要求',
  STAFF: '人员要求',
  FORMAT: '格式要求',
  qualification: '资质要求',
  technical: '技术要求',
  commercial: '商务要求',
  performance: '业绩要求',
  staffing: '人员要求',
  format: '格式要求',
}

export const getCategoryLabel = (category: string): string =>
  categoryLabels[category] || categoryLabels[category.toUpperCase()] || category

/**
 * 招标要求分类的筛选/标签配色（单一数据源）。
 *
 * 约束1（防丢样式）: 必须是完整静态 class 字面量, 禁止运行时拼接
 *   (如 `bg-${c}-50`), 否则 Tailwind JIT 扫描不到会被 purge。
 * 约束2（语义红线）: 类别色板一律不含 red —— red 在本系统强语义为
 *   "否决项 / 失败", 由 MANDATORY_FILTER_CLASSES 独占, 避免撞色误导。
 */
export interface CategoryColorClasses {
  /** 筛选栏未选中态 chip（含 hover） */
  chipInactive: string
  /** 筛选栏选中态 chip（实心高亮） */
  chipActive: string
  /** 列表行内分类标签（浅色, 无 hover） */
  badge: string
  /** 分组标题左侧色点（纯背景） */
  dot: string
}

/** 按分类中文 label 映射, 两套枚举键 (QUAL/qualification 等) 经 getCategoryLabel 归一后共用一色。 */
export const categoryColorClasses: Record<string, CategoryColorClasses> = {
  资质要求: {
    chipInactive: 'bg-blue-50 text-blue-700 border border-blue-200 hover:bg-blue-100',
    chipActive: 'bg-blue-500 text-white border border-blue-500',
    badge: 'bg-blue-50 text-blue-700 border border-blue-200',
    dot: 'bg-blue-500',
  },
  技术要求: {
    chipInactive: 'bg-cyan-50 text-cyan-700 border border-cyan-200 hover:bg-cyan-100',
    chipActive: 'bg-cyan-500 text-white border border-cyan-500',
    badge: 'bg-cyan-50 text-cyan-700 border border-cyan-200',
    dot: 'bg-cyan-500',
  },
  商务要求: {
    chipInactive: 'bg-amber-50 text-amber-700 border border-amber-200 hover:bg-amber-100',
    chipActive: 'bg-amber-500 text-white border border-amber-500',
    badge: 'bg-amber-50 text-amber-700 border border-amber-200',
    dot: 'bg-amber-500',
  },
  业绩要求: {
    chipInactive: 'bg-violet-50 text-violet-700 border border-violet-200 hover:bg-violet-100',
    chipActive: 'bg-violet-500 text-white border border-violet-500',
    badge: 'bg-violet-50 text-violet-700 border border-violet-200',
    dot: 'bg-violet-500',
  },
  人员要求: {
    chipInactive: 'bg-emerald-50 text-emerald-700 border border-emerald-200 hover:bg-emerald-100',
    chipActive: 'bg-emerald-500 text-white border border-emerald-500',
    badge: 'bg-emerald-50 text-emerald-700 border border-emerald-200',
    dot: 'bg-emerald-500',
  },
  格式要求: {
    chipInactive: 'bg-slate-100 text-slate-600 border border-slate-200 hover:bg-slate-200',
    chipActive: 'bg-slate-500 text-white border border-slate-500',
    badge: 'bg-slate-100 text-slate-600 border border-slate-200',
    dot: 'bg-slate-400',
  },
}

/** 未知分类兜底（中性灰）, 保证运行时不出现无样式 chip。 */
export const FALLBACK_CATEGORY_COLOR: CategoryColorClasses = {
  chipInactive: 'bg-gray-100 text-gray-600 border border-gray-200 hover:bg-gray-200',
  chipActive: 'bg-gray-600 text-white border border-gray-600',
  badge: 'bg-gray-100 text-gray-600 border border-gray-200',
  dot: 'bg-gray-400',
}

export const getCategoryColorClasses = (label: string): CategoryColorClasses =>
  categoryColorClasses[label] ?? FALLBACK_CATEGORY_COLOR

/**
 * "全部"筛选项配色（独立中性色相 indigo, 表示全集 / 总览, 不占用任一类别色）。
 * 选 indigo 的理由: ①非黑白灰 —— 此前 gray-800→gray-500→gray-200 反复横跳仍显廉价/突兀;
 *   ②不与任一类别色撞 —— 类别用 blue/cyan/amber/violet/emerald/slate, indigo 均不占;
 *   ③沉稳中性, 契合"全部=总览"语义, 且非业务域强语义色(投标blue/串标iris/标书teal/完成green/失败red)。
 * 视觉对齐彩色分类 chip: 选中态实心 indigo, 未选中态浅 indigo 底。
 */
export const ALL_FILTER_CLASSES = {
  active: 'bg-indigo-500 text-white border border-indigo-500',
  inactive: 'bg-indigo-50 text-indigo-700 border border-indigo-200 hover:bg-indigo-100',
}

/** "仅显示否决项"开关配色（红, 独占否决 / 失败语义, 与类别色互斥; 与分类筛选可叠加）。 */
export const MANDATORY_FILTER_CLASSES = {
  active: 'bg-red-50 text-red-700 border border-red-400',
  inactive: 'bg-white text-red-600 border border-red-300 hover:bg-red-50',
}

/**
 * "仅显示评分项/重点项"开关配色（amber, 与列表行内评分项徽标同语义色）。
 *
 * 色相与"商务要求"类别 chip 同为 amber, 靠形态而非色相区分:
 * 本项是左侧 toggle 组成员(白底 + 方形选中标记), 类别 chip 是右侧单选组
 * 成员(浅色/实心填充, 无标记), 两组之间有竖线分隔。
 * 不另选新色相是为了让"筛选开关"与"行内评分项徽标"保持同色可追溯。
 */
export const SCORING_FOCUS_FILTER_CLASSES = {
  active: 'bg-amber-50 text-amber-800 border border-amber-400',
  inactive: 'bg-white text-amber-700 border border-amber-300 hover:bg-amber-50',
}

export const subRequirementFacetLabels: Record<string, string> = {
  // STAFF
  personnel_cert: '人员证书/职称',
  personnel_social: '社保/劳动合同',
  personnel_perf: '人员业绩',
  personnel_aux: '培训/体检/保险',
  personnel_mgmt: '安全管理/持证上岗',
  // QUAL
  enterprise_qual: '企业资质',
  personnel_cert_in_qual: '人员注册资格',
  qual_system: '管理体系认证',
  qual_financial: '财务/资信证明',
  // PERF
  enterprise_perf: '企业业绩',
  personnel_perf_in_perf: '人员业绩',
  perf_evidence: '业绩证明材料',
  // COMM
  comm_bond: '保证金/担保',
  comm_price: '报价/价格',
  comm_payment: '付款/结算',
  comm_delivery: '工期/交付',
  comm_contract: '合同条款/质保',
}

export const getSubRequirementLabel = (facet: string): string =>
  subRequirementFacetLabels[facet] || facet

export const familyTypeLabels: Record<string, string> = {
  performance: '业绩类',
  audit_finance: '审计财务类',
  qualification: '资质类',
  generic: '通用',
}

export const attributionTypeLabels: Record<string, string> = {
  shared: '共享',
  independent: '独立',
  none: '无',
}

export const consistencyFlagLabels: Record<string, string> = {
  aligned: '已对齐',
  needs_review: '待复核',
  conflicted: '存在冲突',
  cross_conflict: '跨类别冲突',
  unchecked: '未检查',
}

export const topicLabels: Record<string, string> = {
  performance: '业绩',
  qualification: '资质',
  technical: '技术',
  commercial: '商务',
  generic: '通用',
}

export const confidenceLevelLabels: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
  unknown: '未知',
}

export const getConfidenceLabel = (confidence: string): string =>
  confidenceLevelLabels[confidence?.toLowerCase()] || confidence

export const findingDimensionLabels: Record<string, string> = {
  D1: 'D1 人员重叠',
  D2: 'D2 企业关联',
  D3: 'D3 文本雷同',
  D4: 'D4 报价异常',
  D5: 'D5 资质业绩',
  D6: 'D6 文档格式',
  D7: 'D7 异常一致',
  D8: 'D8 文档元数据',
  uncertain: '待确认',
}

export const getFindingDimensionLabel = (dimension: string): string =>
  findingDimensionLabels[dimension] || dimension

/**
 * 跨字段串联命中规则码 -> 中文规则名（单一数据源）。
 *
 * 对应后端 `cross_field_correlator.py` 的 `CrossFieldRule.risk_pattern`。
 * 后端每新增一条跨字段规则，必须同步补充此表，否则前端会裸出英文规则码。
 * 防御测试 `frontend/tests/collusion-enum-label-contract.spec.ts` 会断言两侧一致。
 */
export const crossFieldRiskPatternLabels: Record<string, string> = {
  author_eq_other_legal_rep: 'PDF作者与对方法定代表人一致',
  author_eq_other_company: 'PDF作者与对方公司名称重合',
  author_eq_other_person: 'PDF作者出现在对方项目人员中',
  creator_eq_other_company: 'PDF生成信息与对方公司名称重合',
  legal_rep_eq_other_personnel: '法定代表人出现在对方项目人员中',
  phone_cross_field: '联系电话跨字段一致',
  subcontractor_is_other_bidder: '分包商即对方投标主体',
  bank_eq_other_author: '银行账号与对方PDF作者一致',
  email_user_eq_other_author: '邮箱用户名与对方PDF作者一致',
}

export const getCrossFieldRiskPatternLabel = (pattern: string): string =>
  crossFieldRiskPatternLabels[pattern] || pattern

/**
 * 跨字段串联证据的字段路径 -> 中文字段名（单一数据源）。
 *
 * 对应后端 `identifier_index.py` 的 `FIELD_*` 常量与
 * `cross_field_correlator.py` 的 `_FIELD_LABELS`。
 */
export const sourceFieldPathLabels: Record<string, string> = {
  'company_info.company_name': '投标公司名称',
  'company_info.legal_representative': '法定代表人',
  'company_info.registered_address': '注册地址',
  'company_info.contact_phone': '联系电话',
  'company_info.bank_account': '银行账号',
  'company_info.unified_social_credit_code': '统一信用代码',
  'personnel_list[].name': '项目人员',
  'personnel_list[].certificate_numbers': '项目人员资格证书',
  'personnel_list[].phone': '项目人员手机',
  'personnel_list[].email': '项目人员邮箱',
  subcontractors: '分包商/供应商',
  'metadata_info.author': 'PDF作者',
  'metadata_info.creator_tool': 'PDF生成工具',
  'metadata_info.producer': 'PDF生成器',
  'metadata_info.title': 'PDF标题',
}

export const getSourceFieldLabel = (fieldPath: string): string =>
  sourceFieldPathLabels[fieldPath] || fieldPath
