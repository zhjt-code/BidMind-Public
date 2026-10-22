import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const frontendRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const helpPanelSource = readFileSync(join(frontendRoot, 'src/components/HelpPanel.tsx'), 'utf8')
const helpDir = join(frontendRoot, 'src/components/help')

function readHelpTree(dir: string): string {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return [readHelpTree(path)]
    if (entry.name.endsWith('.ts') || entry.name.endsWith('.tsx')) {
      return [readFileSync(path, 'utf8')]
    }
    return []
  }).join('\n')
}

const helpTree = readHelpTree(helpDir)

test('帮助中心抽屉桌面宽度为整页一半且覆盖默认 max-w-sm', () => {
  assert.match(helpPanelSource, /w-full sm:w-1\/2 sm:max-w-none/)
  assert.doesNotMatch(helpPanelSource, /w-\[420px\]/)
})

test('帮助中心首次打开时所有模块默认折叠', () => {
  assert.match(helpPanelSource, /useState<HelpSectionKey \| null>\(null\)/)
  assert.doesNotMatch(helpPanelSource, /useState<HelpSectionKey \| null>\('quick_start'\)/)
})

test('帮助中心底部展示 6.8.6 版本号且不带 V 前缀', () => {
  assert.match(helpPanelSource, />BidMind AI 6\.8\.6</)
  assert.doesNotMatch(helpPanelSource, /BidMind AI V6\.0\.2/)
})

test('帮助中心指南内步骤默认折叠，支持嵌套展开', () => {
  assert.match(helpPanelSource, /expandedStepId/)
  assert.match(helpPanelSource, /useState<string \| null>\(null\)/)
  assert.match(helpTree, /kind: 'guideSteps'/)
})

test('标书编写指南覆盖目录页关键按钮与废标风险', () => {
  assert.match(helpTree, /新增目录/)
  assert.match(helpTree, /重新生成/)
  assert.match(helpTree, /锁图标/)
  assert.match(helpTree, /拖动排序/)
  assert.match(helpTree, /废标风险/)
  assert.match(helpTree, /writing_outline_row/)
})

test('投标审查指南覆盖确认页关键按钮', () => {
  assert.match(helpTree, /确认并开始审查/)
  assert.match(helpTree, /重新提取/)
  assert.match(helpTree, /否决项/)
  assert.match(helpTree, /跳过招标文件图片识别/)
})

test('串标审查指南覆盖上传并处理与招标文件建议', () => {
  assert.match(helpTree, /上传并处理/)
  assert.match(helpTree, /跳过投标文件图片识别/)
  assert.match(helpTree, /强烈建议同时上传招标文件/)
})

test('知识库指南覆盖可用门禁与改为参考并保存', () => {
  assert.match(helpTree, /改为参考并保存/)
  assert.match(helpTree, /适用流程/)
  assert.match(helpTree, /从知识库引用/)
})

test('快速开始账户菜单与功能卡片同级并覆盖四项入口', () => {
  assert.match(helpTree, /title: '标书编写'/)
  assert.match(helpTree, /title: '账户菜单'/)
  assert.match(helpTree, /title: '知识库'[\s\S]*?title: '账户菜单'/)
  assert.doesNotMatch(helpTree, /右上角账户菜单/)
  assert.match(helpTree, /header_user_menu/)
  assert.match(helpTree, /账户中心/)
  assert.match(helpTree, /消息中心/)
  assert.match(helpTree, /修改密码/)
  assert.match(helpTree, /退出登录/)
})
