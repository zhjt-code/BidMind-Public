import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  PROJECT_TYPE_STYLE,
  STATUS_GROUP_STYLE,
  getDashboardScopeLabel,
} from '../src/pages/projectList/dashboard/projectDashboardConfig.ts'

const dashboardSource = readFileSync(
  new URL('../src/pages/projectList/dashboard/ProjectDashboard.tsx', import.meta.url),
  'utf8',
)
const launchPadSource = readFileSync(
  new URL('../src/pages/projectList/dashboard/NewUserLaunchPad.tsx', import.meta.url),
  'utf8',
)
const trendSource = readFileSync(
  new URL('../src/pages/projectList/dashboard/ProjectCreationTrendChart.tsx', import.meta.url),
  'utf8',
)
const typeStatusSource = readFileSync(
  new URL('../src/pages/projectList/dashboard/ProjectTypeStatusChart.tsx', import.meta.url),
  'utf8',
)
const recentProjectsSource = readFileSync(
  new URL('../src/pages/projectList/dashboard/RecentProjectsPanel.tsx', import.meta.url),
  'utf8',
)
const heroSource = readFileSync(
  new URL('../src/pages/projectList/dashboard/ProjectDashboardHero.tsx', import.meta.url),
  'utf8',
)
const stageSource = readFileSync(
  new URL('../src/pages/projectList/dashboard/ProjectStageChart.tsx', import.meta.url),
  'utf8',
)
const healthSource = readFileSync(
  new URL('../src/pages/projectList/dashboard/ProjectHealthPanel.tsx', import.meta.url),
  'utf8',
)
const windowToggleSource = readFileSync(
  new URL('../src/pages/projectList/dashboard/WindowDaysToggle.tsx', import.meta.url),
  'utf8',
)
const actionQueueSource = readFileSync(
  new URL('../src/pages/projectList/dashboard/ProjectDashboardActionQueue.tsx', import.meta.url),
  'utf8',
)

test('控制台颜色和文案沿用三业务与四状态主题', () => {
  assert.equal(PROJECT_TYPE_STYLE.bid_writing.color, '#14b8a6')
  assert.equal(PROJECT_TYPE_STYLE.bid_review.color, '#2563eb')
  assert.equal(PROJECT_TYPE_STYLE.collusion_check.color, '#9056D8')
  assert.equal(STATUS_GROUP_STYLE.processing.color, '#f97316')
  assert.equal(STATUS_GROUP_STYLE.completed.color, '#22c55e')
  assert.equal(STATUS_GROUP_STYLE.failed.color, '#ef4444')
  assert.equal(getDashboardScopeLabel('mine'), '我的项目')
  assert.equal(getDashboardScopeLabel('all'), '全系统项目')
})

test('控制台图表使用 Recharts 真实图表, 禁止 Radar/Gauge 和假数据', () => {
  assert.match(dashboardSource, /ProjectHealthPanel/)
  assert.doesNotMatch(dashboardSource, /ProjectStatusDonutChart/)
  assert.match(dashboardSource, /ProjectCreationTrendChart/)
  assert.match(dashboardSource, /ProjectTypeStatusChart/)
  assert.match(dashboardSource, /ProjectStageChart/)
  assert.doesNotMatch(dashboardSource + trendSource, /RadarChart|Gauge|mock|fake/i)
  assert.match(trendSource, /LineChart/)
  assert.match(trendSource, /isAnimationActive=\{false\}/)
  assert.match(trendSource, /domain=\{\[0/)
})

test('控制台首屏在中等宽度形成多列看板而非纵向报表流', () => {
  assert.match(dashboardSource, /md:grid-cols-\[minmax\(0,1\.25fr\)_340px\]/)
  assert.match(dashboardSource, /lg:grid-cols-\[minmax\(0,1\.15fr\)_minmax\(360px,0\.85fr\)\]/)
  assert.doesNotMatch(dashboardSource, /xl:grid-cols-\[360px_minmax\(0,1fr\)\]/)
})

test('业务类型状态图例中文化, 最近项目以更新列表呈现', () => {
  assert.match(typeStatusSource, /name=\{STATUS_GROUP_STYLE\[key\]\.label\}/)
  assert.match(typeStatusSource, /Legend/)
  assert.match(typeStatusSource, /数量/)
  assert.match(typeStatusSource, /占比/)
  assert.match(recentProjectsSource, /最近更新/)
  assert.doesNotMatch(recentProjectsSource, /项目动态流/)
  assert.match(recentProjectsSource, /border-l/)
})

test('最近更新在项目名后展示当前处理节点, 业务类型徽标保持右侧', () => {
  assert.match(recentProjectsSource, /PROJECT_TYPE_STYLE/)
  assert.match(recentProjectsSource, /getStatusConfig/)
  assert.match(recentProjectsSource, /const statusConfig = getStatusConfig\(project\.status, project\.projectType\)/)
  assert.match(recentProjectsSource, /<span className="truncate text-sm font-medium text-gray-900">\{project\.name\}<\/span>[\s\S]*\{statusConfig\.label\}/)
  assert.match(recentProjectsSource, /<div className="flex min-w-0 items-center gap-2">/)
  assert.match(recentProjectsSource, /shrink-0 whitespace-nowrap/)
  assert.match(recentProjectsSource, /<div className="flex items-center justify-between gap-3">/)
  assert.match(recentProjectsSource, /\{statusConfig\.label\}/)
  assert.doesNotMatch(recentProjectsSource, /当前节点：/)
})

test('Hero 使用一行指标带展示全部项目与四状态', () => {
  assert.match(heroSource, /MetricStrip/)
  assert.match(heroSource, /sm:grid-cols-5/)
  assert.match(heroSource, /运行总览/)
  assert.match(heroSource, /规模与健康/)
  assert.match(heroSource, /更新/)
  assert.match(heroSource, /RefreshCw/)
  assert.match(heroSource, /min-h-\[260px\]/)
  assert.match(heroSource, /px-5 py-4 sm:px-6/)
  assert.match(heroSource, /flex flex-col gap-4/)
  assert.doesNotMatch(heroSource, /bg-cyan-300\/\[0\.06\]/)
  assert.doesNotMatch(heroSource, /PrimaryMetric/)
  assert.doesNotMatch(heroSource, /getDashboardScopeLabel/)
  assert.doesNotMatch(heroSource, /数据生成/)
  assert.match(heroSource, /label="全部项目"/)
  assert.match(heroSource, /label="待处理"/)
  assert.match(heroSource, /label="处理中"/)
  assert.match(heroSource, /label="已完成"/)
  assert.match(heroSource, /label="失败"/)
  assert.doesNotMatch(heroSource, /待关注/)
  assert.match(heroSource, /onSelect\(\{ clear: true \}\)/)
  assert.match(heroSource, /onSelect\(\{ status: 'pending' \}\)/)
  assert.match(heroSource, /onSelect\(\{ status: 'processing' \}\)/)
  assert.match(heroSource, /onSelect\(\{ status: 'completed' \}\)/)
  assert.match(heroSource, /onSelect\(\{ status: 'failed' \}\)/)
})

test('行动队列融入 Hero 深色区域, 复用图表筛选定位列表', () => {
  assert.match(heroSource, /ProjectDashboardActionQueue/)
  assert.match(heroSource, /items=\{data\.actionQueue\}/)
  assert.match(heroSource, /onSelect=\{onSelect\}/)
  assert.match(actionQueueSource, /需要立即处理/)
  assert.match(actionQueueSource, /失败待处置|item\.label/)
  assert.match(actionQueueSource, /Clock3/)
  assert.match(actionQueueSource, /Upload/)
  assert.doesNotMatch(actionQueueSource, /待处理/)
  assert.doesNotMatch(actionQueueSource, /UploadCloud/)
  assert.match(actionQueueSource, /bg-white\/\[0\.07\]/)
  assert.match(actionQueueSource, /sm:grid-cols-2/)
  assert.doesNotMatch(dashboardSource, /<ProjectDashboardActionQueue/)
  assert.doesNotMatch(actionQueueSource, /from-white via-cyan-50/)
})

test('阶段分布按业务阶段聚合并可下钻成员状态', () => {
  assert.match(stageSource, /当前办理阶段/)
  assert.match(stageSource, /memberStatuses/)
  assert.match(stageSource, /awaiting_confirm/)
  assert.match(stageSource, /#f59e0b/)
  assert.doesNotMatch(stageSource, /#ef4444/)
  assert.match(stageSource, /<Cell/)
  assert.doesNotMatch(stageSource, /通用·/)
})

test('趋势图展示新建与完成并支持窗口切换', () => {
  assert.match(trendSource, /新建 vs 完成/)
  assert.match(trendSource, /dataKey="created"/)
  assert.match(trendSource, /dataKey="completed"/)
  assert.match(trendSource, /#22c55e/)
  assert.match(trendSource, /项目数/)
  assert.match(trendSource, /日期/)
  assert.match(trendSource, /WindowDaysToggle/)
  assert.match(windowToggleSource, /DASHBOARD_WINDOW_DAYS = \[7, 30, 90\]/)
  assert.match(windowToggleSource, /\{days\}天/)
  assert.match(trendSource, /h-80/)
  assert.doesNotMatch(trendSource, /<Line[^>]*onClick/)
})

test('运行健康卡替代状态环图, 业务类型图保留绘图区', () => {
  assert.match(healthSource, /运行健康/)
  assert.match(healthSource, /办结成功率/)
  assert.match(healthSource, /单项目平均耗时/)
  assert.match(healthSource, /60_000/)
  assert.doesNotMatch(healthSource, /不是全部项目合计/)
  assert.doesNotMatch(healthSource, /bg-gradient-to-b/)
  assert.match(healthSource, /未办结项目/)
  assert.match(healthSource, /待处理与处理中/)
  assert.match(healthSource, /WindowDaysToggle/)
  assert.match(healthSource, /onWindowDaysChange/)
  assert.match(healthSource, /近\{windowDays\}天净增/)
  assert.doesNotMatch(healthSource, /近24小时/)
  assert.match(dashboardSource, /trendWindowDays/)
  assert.match(dashboardSource, /healthWindowDays/)
  assert.doesNotMatch(dashboardSource, /windowDays=\{data\.windowDays\}/)
  assert.match(healthSource, /getProjectTypeColor/)
  assert.match(healthSource, /HEALTH_GLASS_CARD/)
  assert.match(healthSource, /bg-white\/50/)
  assert.match(healthSource, /backdrop-blur-sm/)
  assert.match(healthSource, /hover:-translate-y-1/)
  assert.match(healthSource, /hover:shadow-\[0_10px_24px_rgba\(15,23,42,0\.14\)\]/)
  assert.doesNotMatch(healthSource, /border-l-emerald-500/)
  assert.doesNotMatch(healthSource, /border-l-cyan-500/)
  assert.doesNotMatch(healthSource, /bg-slate-100\/70/)
  assert.doesNotMatch(healthSource, /待消化积压/)
  assert.doesNotMatch(healthSource, /P95/)
  assert.match(healthSource, /text-amber-700/)
  assert.doesNotMatch(healthSource, /text-red-/)
  assert.match(typeStatusSource, /h-80/)
  assert.match(typeStatusSource, /verticalAlign="top"/)
  assert.match(typeStatusSource, /PERCENT_AXIS_TICKS/)
  assert.match(typeStatusSource, /countAxisTicks/)
  assert.match(typeStatusSource, /allowDataOverflow/)
  assert.match(typeStatusSource, /toPercentShares/)
  assert.doesNotMatch(typeStatusSource, /dataMax/)
  assert.doesNotMatch(typeStatusSource, /left: -16/)
  assert.match(dashboardSource, /ProjectHealthPanel/)
})

test('图表说明使用用户语言, 不出现下钻或接口技术表达', () => {
  assert.doesNotMatch(healthSource, /下钻/)
  assert.doesNotMatch(trendSource, /下钻/)
  assert.doesNotMatch(stageSource, /实时进度接口/)
})

test('零项目启动台直接复用新建项目弹窗且不展示伪造统计数字', () => {
  assert.match(launchPadSource, /useCreateDialogStore/)
  assert.match(launchPadSource, /type: 'bid_writing'/)
  assert.match(launchPadSource, /type: 'bid_review'/)
  assert.match(launchPadSource, /type: 'collusion_check'/)
  assert.match(launchPadSource, /open\(card\.type\)/)
  assert.doesNotMatch(launchPadSource, /模拟|假数据|mock|fake/i)
})

test('零项目启动台在对应业务卡片展示暗色适配的免费体验角标', () => {
  assert.match(launchPadSource, /getRemainingTrialsForType/)
  assert.match(launchPadSource, /免费体验 \{remaining\} 次/)
  assert.match(launchPadSource, /Gift/)
  assert.match(launchPadSource, /bg-emerald-400\/15/)
  assert.match(launchPadSource, /text-emerald-200/)
  assert.match(launchPadSource, /border-emerald-300\/35/)
  assert.doesNotMatch(launchPadSource, /bg-green-50/)
  assert.doesNotMatch(launchPadSource, /text-green-600/)
})
