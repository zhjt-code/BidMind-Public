/**
 * 职责: 公开首页的单一文案事实源。
 * 不做: 不承诺未核验的安全资质、价格、试用额度；信任指标与耗时口径以业务侧提供的核验数据为准。
 */

export type PublicCapabilityKey = 'writing' | 'review' | 'collusion'

export interface PublicCapability {
  key: PublicCapabilityKey
  title: string
  summary: string
  input: string
  output: string
  accent: 'teal' | 'blue' | 'iris'
}

export interface PublicJourney {
  key: PublicCapabilityKey
  title: string
  steps: string[]
}

export interface HeroJourneyStep {
  key: PublicCapabilityKey
  stepNumber: 1 | 2 | 3
  title: string
  summary: string
  reviewNote: string
  accent: 'teal' | 'blue' | 'iris'
}

export interface TrustMetric {
  key: string
  value: string
  label: string
}

export type PublicDeploymentPathKey = 'local' | 'cloud' | 'hosted'

export interface PublicDeploymentPath {
  key: PublicDeploymentPathKey
  title: string
  fit: string
  boundary: string
  notice: string
}

export const publicHomeContent = {
  hero: {
    eyebrow: '证据驱动的招投标 AI 工作台',
    title: 'AI 逐页读透标书',
    titleAccent: '写标 · 审标 · 查串标，一个平台全部搞定',
    proofLine: '步步有据可查',
    painPoints: [
      {
        key: 'writing' as const,
        label: '标书编写',
        pain: '招标要求散在几十上百页，评分点、章节和企业素材怎么一一对齐？',
        solution: 'AI 提取约束与评分点，匹配企业素材，生成可导出的 Word 草稿',
      },
      {
        key: 'review' as const,
        label: '投标审查',
        pain: '几百页标书中，关键响应漏一项，怎么避免失分甚至废标？',
        solution: 'AI 逐条核对招标要求与投标响应，回链原文页码，生成带证据的审查报告',
      },
      {
        key: 'collusion' as const,
        label: '串标风险',
        pain: '多份投标文件逐一比对，异常相似究竟藏在哪一页、哪一段？',
        solution: 'AI 交叉比对文本、格式与关键字段，归集可复核线索，辅助人工研判',
      },
    ],
    primaryCta: '立即注册',
    secondaryCta: '了解三大能力',
  },
  heroJourneySteps: [
    {
      key: 'collusion' as const,
      stepNumber: 1 as const,
      title: '串标审查',
      summary: '上传即分析 · 报告自动生成',
      reviewNote: '全程 AI 自动',
      accent: 'iris' as const,
    },
    {
      key: 'review' as const,
      stepNumber: 2 as const,
      title: '投标审查',
      summary: '确认招标要求 · 出证据报告',
      reviewNote: '1 个人工审核节点',
      accent: 'blue' as const,
    },
    {
      key: 'writing' as const,
      stepNumber: 3 as const,
      title: '标书编写',
      summary: '约束 / 目录 / 绑定 · 生成可导出 Word',
      reviewNote: '3 个确认节点',
      accent: 'teal' as const,
    },
  ] satisfies HeroJourneyStep[],
  trustMetrics: [
    { key: 'enterprises', value: '60+', label: '服务企业（家）' },
    { key: 'industries', value: '20+', label: '服务行业（类）' },
    { key: 'dailyTasks', value: '2,000+', label: '日均任务处理（次）' },
    { key: 'reports', value: '50,000+', label: '累计交付报告（份）' },
    { key: 'pages', value: '100万+', label: '累计处理文档（页）' },
    { key: 'timeSaved', value: '3 天 → 2 小时', label: '标书编写平均耗时' },
  ] satisfies TrustMetric[],
  capabilities: [
    {
      key: 'writing',
      title: '标书编写',
      summary: '不是从空白页拼材料，而是围绕招标约束、评分重点和企业素材组织章节响应',
      input: '招标文件与企业素材',
      output: '可预览和导出的 Word 文档',
      accent: 'teal',
    },
    {
      key: 'review',
      title: '投标审查',
      summary: '不是只给一个结论，而是先确认招标要求，再把每条响应核对到证据来源',
      input: '招标 PDF 与投标 PDF',
      output: '结构化结果、来源页码与 PDF 报告',
      accent: 'blue',
    },
    {
      key: 'collusion',
      title: '串标风险识别',
      summary: '不是直接认定串通，而是把多份文件中的异常相似线索提前暴露给人工核查',
      input: '2-15 份投标文件',
      output: '风险证据、文件对结果与 PDF 报告',
      accent: 'iris',
    },
  ] satisfies PublicCapability[],
  journeys: [
    {
      key: 'writing',
      title: '标书编写旅程',
      steps: ['提取约束', '调整目录', '处理素材', '确认绑定', '生成与检查', '导出 Word'],
    },
    {
      key: 'review',
      title: '投标审查旅程',
      steps: ['上传文件', '确认招标要求', '提取响应', '逐项对比', '查看结果', '导出 PDF'],
    },
    {
      key: 'collusion',
      title: '串标风险识别旅程',
      steps: ['上传多份文件', '提取关键信息', '按风险分级分析', '查看异常线索', '导出 PDF'],
    },
  ] satisfies PublicJourney[],
  platform: [
    {
      label: '统一账号',
      description: '同一账号管理三类独立项目',
    },
    {
      label: '项目中心',
      description: '项目中心按业务类型筛选',
    },
    {
      label: '知识库',
      description: '知识库可沉淀企业素材与经验',
    },
    {
      label: '任务运维',
      description: '任务进度、失败重试与管理员运维口径统一',
    },
  ],
  platformKnowledge: {
    eyebrow: '知识库',
    title: '沉淀稳定的企业知识，三类任务都能复用',
    summary: '入库后写标、审标、查串标稳定复用，杜绝重复上传',
    footnote: '资料须「可用」。审查要当次改为参考并保存；写标引用后仍须确认绑定。',
    deposits: [
      {
        label: '资质与证书',
        detail: '营业执照、资质证书等稳定证明，新项目引用即可，不必重传',
      },
      {
        label: '企业简介与业绩',
        detail: '公司介绍、人员设备、历史业绩，写标按章引用，减少漏项',
      },
      {
        label: '规范与审查经验',
        detail: '格式模板、法规、白名单与风险模式，辅助审标、抑制误报',
      },
    ],
    libraries: [
      {
        key: 'enterprise' as const,
        title: '企业资料',
        description: '简介、资质、证书、业绩、人员设备',
        use: '写标引用素材',
      },
      {
        key: 'standards' as const,
        title: '规范标准',
        description: '规范、法规、格式模板',
        use: '三类任务参考',
      },
      {
        key: 'experience' as const,
        title: '审查经验',
        description: '审查经验、串标白名单、风险模式',
        use: '审标辅助 / 串标抑制误报',
      },
    ],
  },
  scenarios: [
    {
      title: '投标团队',
      description: '用已有素材组织标书内容，并在提交前检查关键响应',
    },
    {
      title: '审查人员',
      description: '围绕招标要求逐项核对投标文件，查看来源页码和报告',
    },
    {
      title: '风控与监督',
      description: '在多份投标文件之间定位异常相似线索，缩小人工复核范围',
    },
  ],
  trustIntro: '部署方式：本地自建、自有云多点办公，或直接注册使用',
  deploymentPaths: [
    {
      key: 'local',
      title: '本地部署',
      fit: '系统和数据留在自有环境',
      boundary: '使用 Docker Compose 部署在您自己的环境',
      notice: '是否联网、是否走云端 AI，由您的配置决定',
    },
    {
      key: 'cloud',
      title: '云端部署',
      fit: '多点办公，仍要自己控数据',
      boundary: '使用 Docker Compose 部署在您自己的云资源上，不是公共注册实例',
      notice: '若选用云端 AI，内容会发送至您配置的服务商',
    },
    {
      key: 'hosted',
      title: '服务器直接注册',
      fit: '单用户、偶尔使用、预算有限',
      boundary: '使用现有运营服务器，无需自建',
      notice: '数据在运营方服务器处理；项目仍按创建者归属隔离',
    },
  ] satisfies PublicDeploymentPath[],
  trust: [
    '账号登录认证',
    '项目按创建者归属做访问控制',
    '自建与直接注册共用同一套系统',
    'AI 可配置为本地或云端服务',
    '支持配置 HTTPS',
    '记录关键管理操作',
  ],
  finalAction: {
    title: '先从一项真实任务开始，看证据是否足够清楚',
    description: '注册后即可开始写标、审标或风险识别，可用权益以工作台显示为准',
    primaryCta: '立即注册',
    secondaryCta: '查看数据与部署说明',
  },
} as const
