import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  DOMAIN_ACCENT,
  DOMAIN_HEX,
  DOMAIN_HEX_HIGHLIGHT,
  DOMAIN_LABEL,
} from '../src/lib/domainTheme.ts'
import {
  BUSINESS_HUB_ACCENTS,
  getBusinessHubConfig,
} from '../src/pages/projectList/hub/businessHubConfig.ts'
import { PROJECT_TYPE_STYLE } from '../src/pages/projectList/dashboard/projectDashboardConfig.ts'

const headerSource = readFileSync(new URL('../src/components/Header.tsx', import.meta.url), 'utf8')
const typesSource = readFileSync(new URL('../src/pages/projectList/types.ts', import.meta.url), 'utf8')
const projectCardSource = readFileSync(new URL('../src/pages/projectList/ProjectCard.tsx', import.meta.url), 'utf8')
const createDialogSource = readFileSync(new URL('../src/pages/projectList/CreateProjectDialog.tsx', import.meta.url), 'utf8')
const statsBarSource = readFileSync(new URL('../src/pages/projectList/ProjectStatsBar.tsx', import.meta.url), 'utf8')
const launchPadSource = readFileSync(
  new URL('../src/pages/projectList/dashboard/NewUserLaunchPad.tsx', import.meta.url),
  'utf8',
)
const writingStepBarSource = readFileSync(new URL('../src/pages/writing/WritingStepBar.tsx', import.meta.url), 'utf8')
const fileProcessSource = readFileSync(new URL('../src/components/FileProcessProgress.tsx', import.meta.url), 'utf8')
const collusionUploadSource = readFileSync(new URL('../src/pages/CollusionUpload.tsx', import.meta.url), 'utf8')
const fontSource = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8')
const segmentedSource = readFileSync(new URL('../src/components/upload/SegmentedControl.tsx', import.meta.url), 'utf8')
const runModeSource = readFileSync(new URL('../src/components/upload/RunModeOption.tsx', import.meta.url), 'utf8')
const resultViewSwitchSource = readFileSync(
  new URL('../src/pages/result/ResultViewSwitch.tsx', import.meta.url),
  'utf8',
)
const skipVlSource = readFileSync(new URL('../src/components/upload/SkipVlOption.tsx', import.meta.url), 'utf8')
const envelopeSource = readFileSync(new URL('../src/pages/fileUploadParts/EnvelopeModeSwitch.tsx', import.meta.url), 'utf8')
const knowledgeSource = readFileSync(new URL('../src/components/KnowledgeUsagePanel.tsx', import.meta.url), 'utf8')
const fileUploadSource = readFileSync(new URL('../src/pages/FileUpload.tsx', import.meta.url), 'utf8')
const writingUploadSource = readFileSync(new URL('../src/pages/writing/WritingUpload.tsx', import.meta.url), 'utf8')

test('域身份色决策源锁定写 teal / 审 blue / 查 iris', () => {
  assert.equal(DOMAIN_ACCENT.bid_writing, 'teal')
  assert.equal(DOMAIN_ACCENT.bid_review, 'blue')
  assert.equal(DOMAIN_ACCENT.collusion_check, 'iris')
  assert.equal(DOMAIN_HEX.bid_writing, '#14b8a6')
  assert.equal(DOMAIN_HEX.bid_review, '#2563eb')
  assert.equal(DOMAIN_HEX.collusion_check, '#9056D8')
  assert.equal(DOMAIN_HEX_HIGHLIGHT.collusion_check, '#7165EA')
  assert.equal(DOMAIN_HEX_HIGHLIGHT.bid_writing, DOMAIN_HEX.bid_writing)
  assert.equal(DOMAIN_HEX_HIGHLIGHT.bid_review, DOMAIN_HEX.bid_review)
  assert.equal(DOMAIN_LABEL.bid_writing, '标书编写')
  assert.equal(DOMAIN_LABEL.bid_review, '投标审查')
  assert.equal(DOMAIN_LABEL.collusion_check, '串标审查')
})

test('控制台与 Hub 从同一决策源取域身份色', () => {
  assert.equal(PROJECT_TYPE_STYLE.bid_writing.color, DOMAIN_HEX.bid_writing)
  assert.equal(PROJECT_TYPE_STYLE.bid_review.color, DOMAIN_HEX.bid_review)
  assert.equal(PROJECT_TYPE_STYLE.collusion_check.color, DOMAIN_HEX.collusion_check)
  assert.equal(getBusinessHubConfig('bid_writing').theme.accent, DOMAIN_ACCENT.bid_writing)
  assert.equal(getBusinessHubConfig('bid_review').theme.accent, DOMAIN_ACCENT.bid_review)
  assert.equal(getBusinessHubConfig('collusion_check').theme.accent, DOMAIN_ACCENT.collusion_check)
  assert.deepEqual([...BUSINESS_HUB_ACCENTS].sort(), ['blue', 'iris', 'teal'])
})

test('项目列表消费方不再用 violet 充当写标域色，串标改走 iris', () => {
  assert.match(typesSource, /bid_writing:[\s\S]*?bg-teal-50/)
  assert.match(typesSource, /collusion_check:[\s\S]*?bg-\[#F6F1FC\] text-\[#6C40A2\]/)
  assert.doesNotMatch(typesSource, /bid_writing:[\s\S]*?bg-violet-50/)
  assert.doesNotMatch(typesSource, /collusion_check:[\s\S]*?bg-orange-50/)
  assert.match(projectCardSource, /bid_writing'\s*\?\s*'bg-teal-500'/)
  assert.match(projectCardSource, /collusion_check'\s*\?\s*'bg-\[#9056D8\]'/)
  assert.match(createDialogSource, /hover:border-teal-400 hover:bg-teal-50/)
  assert.match(createDialogSource, /hover:border-\[#9056D8\] hover:bg-\[#F6F1FC\]/)
  assert.match(statsBarSource, /text-teal-500/)
  assert.match(statsBarSource, /text-orange-600/)
  assert.match(statsBarSource, /text-\[#9056D8\]/)
  assert.match(launchPadSource, /from-teal-500 to-teal-600/)
  assert.match(launchPadSource, /from-\[#7165EA\] to-\[#9056D8\]/)
})

test('项目级分段选中态统一蓝底白字，页内分段器保持安静选中', () => {
  assert.match(segmentedSource, /emphasized/)
  assert.match(segmentedSource, /bg-blue-500 text-white shadow-sm/)
  assert.match(segmentedSource, /bg-white text-\[#1c2733\] shadow-sm/)
  assert.doesNotMatch(segmentedSource, /bg-teal-500 text-white/)
  assert.doesNotMatch(segmentedSource, /bg-orange-500 text-white/)
  assert.doesNotMatch(segmentedSource, /hover:bg-blue-600/)
  assert.match(runModeSource, /emphasized/)
  assert.doesNotMatch(runModeSource, /resolveDomainAccent/)
  assert.match(resultViewSwitchSource, /emphasized/)
  assert.doesNotMatch(resultViewSwitchSource, /resolveDomainAccent/)
  assert.match(fileUploadSource, /<RunModeOption/)
  assert.match(writingUploadSource, /<RunModeOption/)
  assert.doesNotMatch(skipVlSource, /emphasized/)
  assert.doesNotMatch(envelopeSource, /emphasized/)
  assert.doesNotMatch(knowledgeSource, /emphasized/)
})

test('编写步骤条与串标上传进度条跟随域身份色', () => {
  assert.match(writingStepBarSource, /bg-teal-500/)
  assert.match(writingStepBarSource, /bg-teal-50 text-teal-700/)
  assert.doesNotMatch(writingStepBarSource, /bg-primary/)
  assert.match(fileProcessSource, /accentColor = 'blue'/)
  assert.match(collusionUploadSource, /<FileProcessProgress accentColor="iris"/)
  assert.equal((collusionUploadSource.match(/<FileProcessProgress accentColor="iris"/g) ?? []).length, 4)
})

test('工作台顶栏导航禁止 CJK 竖排折行，窄屏仅图标', () => {
  assert.match(headerSource, /whitespace-nowrap/)
  assert.match(headerSource, /hidden xl:inline/)
})

test('全站字体栈使用系统 CJK，不再声明未加载的 Inter', () => {
  assert.match(fontSource, /PingFang SC/)
  assert.match(fontSource, /Microsoft YaHei/)
  assert.doesNotMatch(fontSource, /Inter/)
})
