import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const appSource = readFileSync(resolve('src/App.tsx'), 'utf8')
const contextBarSource = readFileSync(resolve('src/components/ProjectContextBar.tsx'), 'utf8')
const flowLayoutSource = readFileSync(resolve('src/components/ProjectFlowLayout.tsx'), 'utf8')

test('项目流程路由应统一挂载 ProjectFlowLayout', () => {
  assert.match(appSource, /import\('\.\/components\/ProjectFlowLayout'\)/)
  assert.match(appSource, /path="project\/:id"\s+element=\{<ProjectFlowLayout\s*\/>\}/)
})

test('启动脚本不静态引入业务布局和充值弹窗', () => {
  assert.match(appSource, /import\('\.\/components\/Layout'\)/)
  assert.match(appSource, /import\('\.\/components\/AdminLayout'\)/)
  assert.doesNotMatch(appSource, /pages\/billing\/RechargeDialog/)
  assert.doesNotMatch(appSource, /import Layout from/)
  assert.doesNotMatch(appSource, /import RechargeDialog from/)
})

test('项目上下文应提供按类型返回项目列表的稳定路径', async () => {
  const { getProjectListReturnPath } = await import('../src/components/projectFlowContext.ts')

  assert.equal(getProjectListReturnPath('bid_review'), '/app?type=bid_review')
  assert.equal(getProjectListReturnPath('collusion_check'), '/app?type=collusion_check')
  assert.equal(getProjectListReturnPath('bid_writing'), '/app?type=bid_writing')
  assert.equal(getProjectListReturnPath(undefined), '/app')
})

test('项目上下文应从流程路径识别业务域标签', async () => {
  const { getFlowTypeFromPath } = await import('../src/components/projectFlowContext.ts')

  assert.equal(getFlowTypeFromPath('/project/7/upload'), 'bid_review')
  assert.equal(getFlowTypeFromPath('/project/7/requirements'), 'bid_review')
  assert.equal(getFlowTypeFromPath('/project/7/collusion/result'), 'collusion_check')
  assert.equal(getFlowTypeFromPath('/project/7/writing/preview'), 'bid_writing')
  assert.equal(getFlowTypeFromPath('/knowledge-base'), null)
})

test('项目上下文条应只保留返回列表与项目名称', () => {
  assert.doesNotMatch(contextBarSource, /当前项目/)
  assert.doesNotMatch(contextBarSource, /返回项目列表/)
  assert.doesNotMatch(contextBarSource, /ArrowLeft/)
  assert.doesNotMatch(contextBarSource, /FolderOpen/)
  assert.match(contextBarSource, /CornerUpLeft/)
  assert.doesNotMatch(contextBarSource, /PROJECT_FLOW_META/)
  assert.doesNotMatch(contextBarSource, /状态/)
  assert.doesNotMatch(contextBarSource, /项目ID/)
  assert.doesNotMatch(contextBarSource, /shadow-sm/)
  assert.doesNotMatch(contextBarSource, /rounded-xl/)
  assert.doesNotMatch(contextBarSource, /flex-col gap-3/)
  assert.match(contextBarSource, /返回列表/)
  assert.match(contextBarSource, /项目名称/)
})

test('项目上下文条应支持自动隐藏与悬停聚焦展开', () => {
  assert.match(contextBarSource, /INITIAL_HIDE_MS/)
  assert.match(contextBarSource, /HIDE_DELAY_MS/)
  assert.match(contextBarSource, /useState\(true\)/)
  assert.match(contextBarSource, /getProjectContextSeenKey/)
  assert.match(contextBarSource, /sessionStorage/)
  assert.match(contextBarSource, /onMouseEnter=\{showBar\}/)
  assert.match(contextBarSource, /onMouseLeave=\{scheduleHideBar\}/)
  assert.match(contextBarSource, /onFocus=\{showBar\}/)
  assert.match(contextBarSource, /onBlur=\{handleBlur\}/)
  assert.match(contextBarSource, /max-h-0/)
  assert.match(contextBarSource, /max-h-9/)
  assert.match(contextBarSource, /h-3/)
  assert.doesNotMatch(contextBarSource, /bg-gray-300/)
})

test('项目流程布局应局部收紧全局 main 顶部留白', () => {
  assert.match(flowLayoutSource, /-mt-8/)
  assert.match(flowLayoutSource, /projectId=\{hasValidProjectId \? projectId : undefined\}/)
})
