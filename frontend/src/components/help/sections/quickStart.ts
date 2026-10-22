import {
  FolderOpen, RefreshCw, FileSearch, FileText, ShieldAlert, PenTool, BookOpen, User,
} from 'lucide-react'
import type { HelpBlock } from '../types'

export const QUICK_START_BLOCKS: HelpBlock[] = [
  {
    kind: 'paragraph',
    text: 'BidMind AI 围绕招投标全流程提供三大核心功能，并以「知识库」沉淀企业资料。顶部导航进入对应工作台后，点「新建项目」并选择类型即可开始。右上角点头像或用户名，可打开账户菜单（账户中心、消息中心、修改密码、退出登录）。每条流程都有必须由人把关的节点——这些节点决定 AI 后续对错，请对照原文核实，不要一键跳过。',
  },
  {
    kind: 'cards',
    items: [
      { icon: FileText, color: 'text-blue-600', bgColor: 'bg-blue-50/60', title: '投标审查', description: '对标招标文件，逐项审查投标文件的响应情况。唯一人工节点：确认招标要求。' },
      { icon: ShieldAlert, color: 'text-[#6C40A2]', bgColor: 'bg-[#F6F1FC]', title: '串标审查', description: '多份投标文件交叉对比，识别潜在串通。上传后全程自动，结论须人工复核。' },
      { icon: PenTool, color: 'text-orange-600', bgColor: 'bg-orange-50/60', title: '标书编写', description: '提取招标约束，匹配素材并生成投标文件。约束、目录、绑定三处必须人工确认。' },
      { icon: BookOpen, color: 'text-indigo-600', bgColor: 'bg-indigo-50/60', title: '知识库', description: '沉淀企业资料、规范与经验，供三大功能复用。未「可用」且未授权的资料不会生效。' },
      {
        icon: User,
        color: 'text-slate-600',
        bgColor: 'bg-slate-50/60',
        title: '账户菜单',
        description: '点右上角头像展开。可进入账户中心、消息中心、修改密码或退出登录。',
        id: 'header_user_menu',
        summary: '菜单顶部是当前登录用户名和邮箱，只作身份确认，不是可点入口。下面四项才是操作。',
        figures: [{
          id: 'header_user_menu',
          caption: '1 余额、试用与消耗　2 站内信与广播　3 当前会话内改密　4 结束本机登录',
        }],
        actions: [
          {
            name: '账户中心',
            when: '要看余额、免费试用余量、充值或核对消耗时。',
            how: '点菜单「账户中心」。仅在开通计费时出现；未开通则没有这一项。',
            after: '进入账户页：可用余额（或新用户试用说明）、累计充值/消耗，以及账务流水、充值订单、消耗记录三个页签。可支付时还能从本页发起充值。',
          },
          {
            name: '消息中心',
            when: '要读站内信、系统广播或反馈回复时。',
            how: '点菜单「消息中心」。有未读时，帮助按钮左侧还会出现铃铛，点铃铛可先看最近几条，再「查看全部」。',
            after: '进入完整列表，可按全部 / 未读 / 广播 / 反馈筛选，单条或全部标记已读。',
          },
          {
            name: '修改密码',
            when: '首次用临时密码登录后，或需要更换当前密码时。',
            how: '点菜单「修改密码」，填写当前密码和新密码（至少 8 位，须含字母和数字），再确认一次。新密码不能与当前密码相同。',
            after: '改密成功后仍保持登录，无需重新进系统。',
          },
          {
            name: '退出登录',
            when: '结束本机会话，或要把账号交给他人使用前。',
            how: '点菜单最下方「退出登录」。',
            after: '清除本机登录状态，回到公开首页。未保存的页面编辑会丢失，进行中的后台任务仍会继续。',
            risk: '只结束当前浏览器会话，不会取消已在跑的解析或 AI 任务。',
          },
        ],
        notes: [
          '管理员账号在头像旁会显示「管理员」标记；管理后台入口在顶部主导航，不在这个下拉菜单里。',
          '余额低于阈值且免费试用已用完时，工具区会出现「余额不足」提示，点它同样进入账户中心。',
        ],
      },
    ],
  },
  {
    kind: 'steps',
    items: [
      {
        icon: FolderOpen,
        title: '新建项目',
        description: '在对应工作台点击顶部「新建项目」，选择项目类型（投标审查 / 串标审查 / 标书编写）。类型选错会进入另一套页面，创建后不能改类型，只能新建。',
      },
      {
        icon: RefreshCw,
        title: '后台处理，可离开',
        description: '文档解析与 AI 分析较耗时。处理期间可关闭页面，稍后从项目列表点回同一项目查看进度。不要在同一项目里重复点「上传并处理」，以免任务重叠。',
      },
      {
        icon: FileSearch,
        title: '点页码溯源',
        description: '审查、串标、写标结果里的页码大多可点，会打开 PDF 原文对照。AI 引用不准确时，以原文为准。',
      },
    ],
  },
  {
    kind: 'callout',
    tone: 'warning',
    text: 'AI 的提取、审查与生成结果均为辅助参考。正式投标、废标判断或对外结论，必须经人工复核。',
  },
]
