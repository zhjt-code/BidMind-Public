import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const projectFiltersSource = readFileSync(
  new URL('../src/pages/projectList/ProjectFilters.tsx', import.meta.url),
  'utf8',
)
const projectListSource = readFileSync(
  new URL('../src/pages/ProjectList.tsx', import.meta.url),
  'utf8',
)
const headerSource = readFileSync(
  new URL('../src/components/Header.tsx', import.meta.url),
  'utf8',
)
const statsBarSource = readFileSync(
  new URL('../src/pages/projectList/ProjectStatsBar.tsx', import.meta.url),
  'utf8',
)
const headerToolbarSource = readFileSync(
  new URL('../src/pages/projectList/ProjectListHeaderToolbar.tsx', import.meta.url),
  'utf8',
)
const projectListContentSource = readFileSync(
  new URL('../src/pages/projectList/ProjectListContent.tsx', import.meta.url),
  'utf8',
)
const emptyStateSource = readFileSync(
  new URL('../src/components/EmptyState.tsx', import.meta.url),
  'utf8',
)
const projectListDerivationsSource = readFileSync(
  new URL('../src/pages/projectList/projectListDerivations.ts', import.meta.url),
  'utf8',
)
const dashboardFilterBarSource = readFileSync(
  new URL('../src/pages/projectList/dashboard/ProjectDashboardFilterBar.tsx', import.meta.url),
  'utf8',
)

test('项目类型入口只保留在 Header, 页面筛选器不再渲染 Tabs', () => {
  assert.match(headerSource, /label: '控制台'/)
  assert.match(headerSource, /label: '控制台', icon: House/)
  assert.match(headerSource, /label: '投标审查', icon: ClipboardCheck/)
  assert.match(headerSource, /h-\[18px\] w-\[18px\] shrink-0/)
  assert.doesNotMatch(headerSource, /label: '项目中心'/)
  assert.match(headerSource, /typeParam: 'bid_writing'/)
  assert.match(headerSource, /typeParam: 'bid_review'/)
  assert.match(headerSource, /typeParam: 'collusion_check'/)
  assert.doesNotMatch(projectFiltersSource, /@\/components\/ui\/tabs/)
  assert.doesNotMatch(projectFiltersSource, /TabsList/)
  assert.doesNotMatch(projectFiltersSource, /TabsTrigger/)
  assert.doesNotMatch(projectFiltersSource, /TAB_CONFIG/)
})

test('项目列表标题操作区保留批量管理和新建项目', () => {
  assert.match(headerToolbarSource, /批量管理/)
  assert.match(headerToolbarSource, /取消选择/)
  assert.match(headerToolbarSource, /新建项目/)
  assert.match(headerToolbarSource, /onToggleSelectionMode/)
  assert.match(headerToolbarSource, /onCreateProject/)
})

test('项目列表标题区应对齐知识库页头的轻量层级, 不重复展示总数', () => {
  assert.match(headerToolbarSource, /House/)
  assert.match(projectListDerivationsSource, /iconName: 'House'/)
  assert.match(headerToolbarSource, /ClipboardCheck/)
  assert.match(projectListDerivationsSource, /iconName: 'ClipboardCheck'/)
  assert.match(headerToolbarSource, /text-xl font-semibold/)
  assert.match(headerToolbarSource, /flex items-center gap-2 flex-wrap/)
  assert.match(headerToolbarSource, /text-sm text-gray-500/)
  assert.doesNotMatch(headerToolbarSource, /text-sm text-gray-500 mt-1/)
  assert.doesNotMatch(headerToolbarSource, /共 \{context\.total\} 个/)
  assert.doesNotMatch(headerToolbarSource, /text-2xl/)
})

test('控制台通用说明不提及仅管理员可见的负责人筛选', () => {
  assert.match(projectListDerivationsSource, /查看项目运行态势，按业务和状态快速定位项目/)
  assert.doesNotMatch(projectListDerivationsSource, /按业务类型、状态和负责人筛选/)
})

test('业务类型页零项目空状态应使用对应流程文案', () => {
  assert.match(projectListContentSource, /开始您的第一个标书编写项目/)
  assert.match(projectListContentSource, /开始您的第一个投标审查项目/)
  assert.match(projectListContentSource, /开始您的第一个串标审查项目/)
  assert.doesNotMatch(projectListContentSource, /开始您的第一个标书审查/)
})

test('业务类型页零项目引导文案在桌面宽度下不应被通用窄宽限制换行', () => {
  assert.match(emptyStateSource, /descriptionClassName\?: string/)
  assert.match(emptyStateSource, /\$\{descriptionClassName\}/)
  assert.match(projectListContentSource, /descriptionClassName="max-w-full sm:max-w-none sm:whitespace-nowrap"/)
})

test('项目列表顶部和统计条外层间距保持紧凑', () => {
  assert.match(projectListSource, /className="space-y-3 -mt-3"/)
  assert.doesNotMatch(projectListSource, /className="space-y-4"/)
  assert.match(statsBarSource, /className="bg-white rounded-lg shadow px-2 py-1"/)
  assert.doesNotMatch(statsBarSource, /bg-white rounded-lg shadow mb-4 px-2 py-2/)
  assert.doesNotMatch(statsBarSource, /SkeletonStatCards count=\{4\} className="mb-4"/)
})

test('根路径项目中心接入控制台, 业务类型页走 Business Hub', () => {
  assert.match(projectListSource, /ProjectDashboard/)
  assert.match(projectListSource, /NewUserLaunchPad/)
  assert.match(projectListSource, /view\.activeTab === 'all'/)
  assert.match(projectListSource, /isBusinessHub = view\.activeTab !== 'all'/)
  assert.match(projectListSource, /BusinessHubHero/)
  assert.doesNotMatch(projectListSource, /ProjectStatsBar/)
})

test('控制台下方列表应有明确下钻语义标题', () => {
  assert.match(projectListSource, /项目列表/)
  assert.match(projectListSource, /展开列表/)
  assert.match(projectListSource, /isProjectListExpanded/)
  assert.doesNotMatch(projectListSource, /图表筛选会同步到这里/)
})

test('控制台列表折叠状态不应被导航或 URL 恢复自动展开', () => {
  assert.match(
    projectListSource,
    /const handleDashboardFilter = \(patch: DashboardFilterPatch\) => \{\s+setProjectListExpanded\(true\)\s+view\.applyDashboardFilter\(patch\)/,
  )
  assert.doesNotMatch(
    projectListSource,
    /if \(!isConsoleRoot\) \{\s+setProjectListExpanded\(true\)\s+\}/,
  )
  assert.doesNotMatch(
    projectListSource,
    /if \(view\.dashboardFilter\.hasDashboardFilter\) \{\s+setProjectListExpanded\(true\)\s+\}/,
  )
})

test('控制台筛选文案面向用户并位于项目列表区域', () => {
  assert.doesNotMatch(projectListSource, /当前下钻/)
  assert.match(projectListSource, /ProjectDashboardFilterBar/)
  assert.match(projectListSource, /handleToggleSelectionMode/)
  assert.match(projectListSource, /<ProjectDashboardFilterBar/)
  assert.doesNotMatch(projectListSource, /border-t border-slate-100 p-3[\\s\\S]*ProjectDashboardFilterBar/)
  assert.match(dashboardFilterBarSource, /取消筛选/)
  assert.match(dashboardFilterBarSource, /getStatusConfig\(status\)\.label/)
  assert.doesNotMatch(dashboardFilterBarSource, /当前筛选：/)
  assert.doesNotMatch(dashboardFilterBarSource, />清除</)
})

test('批量管理进入选择模式后列表面板同步提供取消选择入口', () => {
  assert.match(projectListSource, /management\.selectionMode/)
  assert.match(projectListSource, /取消选择/)
  assert.match(projectListSource, /收起列表/)
})

test('用户名搜索只对管理员渲染, 普通用户 DOM 不创建输入框', () => {
  assert.match(projectFiltersSource, /isAdmin && onUsernameSearchChange && \(/)
  assert.match(projectFiltersSource, /placeholder="搜索用户名\.\.\."/)
  assert.doesNotMatch(projectFiltersSource, /hidden.*搜索用户名/)
  assert.doesNotMatch(projectFiltersSource, /disabled.*搜索用户名/)
})

test('筛选栏提供更新时间筛选, 且不再挂载常用芯片', () => {
  assert.match(projectFiltersSource, /更新时间/)
  assert.match(projectFiltersSource, /updatedWithin/)
  assert.doesNotMatch(projectFiltersSource, /排序:/)
  assert.doesNotMatch(projectListSource, /SavedFilterChips/)
  assert.doesNotMatch(projectListSource, /保存当前条件|onSaveCurrent/)
})

test('状态/时间/排序三个 Select 触发器带静态语义图标', () => {
  assert.match(projectFiltersSource, /ListFilter/)
  assert.match(projectFiltersSource, /CalendarClock/)
  assert.match(projectFiltersSource, /ArrowDownAZ/)
  assert.match(
    projectFiltersSource,
    /SelectTrigger className="w-full lg:w-\[140px\]"[\s\S]*ListFilter[\s\S]*SelectValue placeholder="全部状态"/,
  )
  assert.match(
    projectFiltersSource,
    /SelectTrigger className="w-full lg:w-\[140px\]"[\s\S]*CalendarClock[\s\S]*SelectValue placeholder="更新时间"/,
  )
  assert.match(
    projectFiltersSource,
    /SelectTrigger className="w-full lg:w-\[150px\]"[\s\S]*ArrowDownAZ[\s\S]*SelectValue placeholder="排序方式"/,
  )
  // 禁止用直接子 span 包前缀行（会被 [&>span]:line-clamp-1 改成纵向）
  assert.match(projectFiltersSource, /div className="flex min-w-0 items-center gap-1\.5"/)
  assert.doesNotMatch(
    projectFiltersSource,
    /SelectTrigger[\s\S]*span className="flex min-w-0 items-center gap-1\.5"/,
  )
})
