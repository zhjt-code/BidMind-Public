/**
 * 结果页左栏/中栏右缘拖拽改宽：宽度计算纯函数 + 页面接线契约。
 * 仓库测试用 node --test（无 JSX 渲染），组件行为以源码契约锁定。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  RESULT_LEFT_PANE_COLLUSION_GROUP_PERCENT,
  RESULT_LEFT_PANE_DEFAULT_PERCENT,
  RESULT_LEFT_PANE_MAX_PERCENT,
  RESULT_LEFT_PANE_MIN_PERCENT,
  RESULT_MIDDLE_PANE_COLLUSION_GROUP_PERCENT,
  RESULT_MIDDLE_PANE_DEFAULT_PERCENT,
  RESULT_MIDDLE_PANE_MAX_PERCENT,
  RESULT_MIDDLE_PANE_MIN_PERCENT,
  clampLeftPanePercent,
  clampMiddlePanePercent,
  clampPanePercent,
  computeLeftPanePercent,
  computeMiddlePanePercent,
  computePanePercent,
} from '../src/pages/resultMiddlePaneWidth.ts'

const resultPageSource = readFileSync(
  new URL('../src/pages/Result.tsx', import.meta.url),
  'utf8',
)
const collusionPageSource = readFileSync(
  new URL('../src/pages/CollusionResult.tsx', import.meta.url),
  'utf8',
)
const percentPaneSource = readFileSync(
  new URL('../src/pages/ResizablePercentPane.tsx', import.meta.url),
  'utf8',
)
const middlePaneSource = readFileSync(
  new URL('../src/pages/ResizableMiddlePane.tsx', import.meta.url),
  'utf8',
)
const leftPaneSource = readFileSync(
  new URL('../src/pages/ResizableLeftPane.tsx', import.meta.url),
  'utf8',
)
const layoutSource = readFileSync(
  new URL('../src/pages/resultPageLayout.ts', import.meta.url),
  'utf8',
)

test('宽度计算模块与组件文件名不得仅大小写不同', () => {
  assert.match(resultPageSource, /from '\.\/ResizableMiddlePane'/)
  assert.match(resultPageSource, /from '\.\/ResizableLeftPane'/)
  assert.match(resultPageSource, /from '\.\/resultMiddlePaneWidth'/)
  assert.equal(
    './ResizableMiddlePane'.toLowerCase() === './resultMiddlePaneWidth'.toLowerCase(),
    false,
  )
  assert.equal(
    './ResizableLeftPane'.toLowerCase() === './resultMiddlePaneWidth'.toLowerCase(),
    false,
  )
  assert.equal(
    './ResizablePercentPane'.toLowerCase() === './resultMiddlePaneWidth'.toLowerCase(),
    false,
  )
  assert.equal(
    leftPaneSource.includes("from './resultLeftPaneWidth'"),
    false,
    '禁止新增 resultLeftPaneWidth.ts，Windows 会与 ResizableLeftPane 撞名',
  )
})

test('默认宽度锁定当前比例：中栏 30/28，左栏 20/22', () => {
  assert.equal(RESULT_MIDDLE_PANE_DEFAULT_PERCENT, 30)
  assert.equal(RESULT_MIDDLE_PANE_COLLUSION_GROUP_PERCENT, 28)
  assert.equal(RESULT_LEFT_PANE_DEFAULT_PERCENT, 20)
  assert.equal(RESULT_LEFT_PANE_COLLUSION_GROUP_PERCENT, 22)
})

test('clampMiddlePanePercent 应将越界宽度压进上下限', () => {
  assert.equal(clampMiddlePanePercent(10), RESULT_MIDDLE_PANE_MIN_PERCENT)
  assert.equal(clampMiddlePanePercent(80), RESULT_MIDDLE_PANE_MAX_PERCENT)
  assert.equal(clampMiddlePanePercent(30), 30)
  assert.equal(clampMiddlePanePercent(28), 28)
})

test('clampLeftPanePercent 应将越界宽度压进上下限', () => {
  assert.equal(clampLeftPanePercent(5), RESULT_LEFT_PANE_MIN_PERCENT)
  assert.equal(clampLeftPanePercent(80), RESULT_LEFT_PANE_MAX_PERCENT)
  assert.equal(clampLeftPanePercent(20), 20)
  assert.equal(clampLeftPanePercent(22), 22)
})

test('clampPanePercent 应按传入上下限钳制', () => {
  assert.equal(clampPanePercent(10, 12, 40), 12)
  assert.equal(clampPanePercent(50, 12, 40), 40)
  assert.equal(clampPanePercent(20, 12, 40), 20)
})

test('computeMiddlePanePercent 应按指针相对中栏左缘与父容器宽度计算百分比', () => {
  // 父宽 1000，中栏左缘 200，指针 500 → 中栏宽 300 → 30%
  assert.equal(computeMiddlePanePercent(500, 200, 1000), 30)
})

test('computeLeftPanePercent 应按指针相对左栏左缘与父容器宽度计算百分比', () => {
  // 父宽 1000，左栏左缘 0，指针 200 → 左栏宽 200 → 20%
  assert.equal(computeLeftPanePercent(200, 0, 1000), 20)
})

test('computePanePercent 在父宽无效时应回退调用方给定的 fallback', () => {
  assert.equal(computePanePercent(400, 0, 0, 12, 40, 22), 22)
  assert.equal(computePanePercent(400, 0, -10, 16, 55, 28), 28)
})

test('computeMiddlePanePercent 在父宽无效时应回退默认 30%', () => {
  assert.equal(computeMiddlePanePercent(400, 0, 0), RESULT_MIDDLE_PANE_DEFAULT_PERCENT)
  assert.equal(computeMiddlePanePercent(400, 0, -10), RESULT_MIDDLE_PANE_DEFAULT_PERCENT)
})

test('computeMiddlePanePercent 拖到两端时应钳制而非溢出', () => {
  assert.equal(computeMiddlePanePercent(100, 200, 1000), RESULT_MIDDLE_PANE_MIN_PERCENT)
  assert.equal(computeMiddlePanePercent(900, 200, 1000), RESULT_MIDDLE_PANE_MAX_PERCENT)
})

test('computeLeftPanePercent 拖到两端时应钳制而非溢出', () => {
  assert.equal(computeLeftPanePercent(-100, 0, 1000), RESULT_LEFT_PANE_MIN_PERCENT)
  assert.equal(computeLeftPanePercent(900, 0, 1000), RESULT_LEFT_PANE_MAX_PERCENT)
})

test('右栏布局常量应吃掉剩余宽度，避免栏改宽后三栏百分比之和溢出', () => {
  assert.match(layoutSource, /RESULT_PAGE_DETAIL_PANE_CLASS/)
  assert.match(layoutSource, /flex-1 min-w-0 border-l/)
})

test('ResizablePercentPane 右缘应为可拖动手柄，宽度走百分比 style', () => {
  assert.match(percentPaneSource, /cursor-col-resize/)
  assert.match(percentPaneSource, /onPointerDown/)
  assert.match(percentPaneSource, /setPointerCapture/)
  assert.match(percentPaneSource, /style=\{\{\s*width:/)
  assert.match(percentPaneSource, /defaultWidthPercent/)
  assert.equal(percentPaneSource.includes('localStorage'), false)
})

test('ResizableMiddlePane 应把中栏默认上下限与手柄契约交给共享栏', () => {
  assert.match(middlePaneSource, /ResizablePercentPane/)
  assert.match(middlePaneSource, /RESULT_MIDDLE_PANE_MIN_PERCENT/)
  assert.match(middlePaneSource, /RESULT_MIDDLE_PANE_MAX_PERCENT/)
  assert.match(middlePaneSource, /result-middle-pane-resizer/)
  assert.match(middlePaneSource, /调整列表宽度/)
})

test('ResizableLeftPane 应把左栏默认上下限、右边框与手柄契约交给共享栏', () => {
  assert.match(leftPaneSource, /ResizablePercentPane/)
  assert.match(leftPaneSource, /RESULT_LEFT_PANE_MIN_PERCENT/)
  assert.match(leftPaneSource, /RESULT_LEFT_PANE_MAX_PERCENT/)
  assert.match(leftPaneSource, /result-left-pane-resizer/)
  assert.match(leftPaneSource, /调整导航宽度/)
  assert.match(leftPaneSource, /border-r/)
  assert.equal(leftPaneSource.includes('localStorage'), false)
})

test('投标审查结果页中栏应接入可拖拽组件且右栏改为弹性剩余宽', () => {
  assert.match(resultPageSource, /ResizableMiddlePane/)
  assert.match(resultPageSource, /RESULT_MIDDLE_PANE_DEFAULT_PERCENT/)
  assert.match(resultPageSource, /RESULT_PAGE_DETAIL_PANE_CLASS/)
  assert.equal(resultPageSource.includes('w-[30%]'), false)
  assert.equal(resultPageSource.includes('w-[50%]'), false)
})

test('投标审查结果页左栏应接入可拖拽组件且默认 20%', () => {
  assert.match(resultPageSource, /ResizableLeftPane/)
  assert.match(resultPageSource, /RESULT_LEFT_PANE_DEFAULT_PERCENT/)
  assert.equal(resultPageSource.includes('w-[20%]'), false)
  assert.equal(resultPageSource.includes('w-[22%]'), false)
})

test('串标结果页群组栏与旧 pair 栏均应接入可拖拽中栏', () => {
  assert.match(collusionPageSource, /ResizableMiddlePane/)
  assert.match(collusionPageSource, /RESULT_MIDDLE_PANE_COLLUSION_GROUP_PERCENT/)
  assert.match(collusionPageSource, /RESULT_MIDDLE_PANE_DEFAULT_PERCENT/)
  assert.match(collusionPageSource, /RESULT_PAGE_DETAIL_PANE_CLASS/)
  assert.equal(collusionPageSource.includes('w-[28%]'), false)
  assert.equal(collusionPageSource.includes('w-[30%]'), false)
  assert.equal(collusionPageSource.includes('w-[50%]'), false)
})

test('串标结果页群组左栏与旧 pair 左栏均应接入可拖拽左栏', () => {
  assert.match(collusionPageSource, /ResizableLeftPane/)
  assert.match(collusionPageSource, /RESULT_LEFT_PANE_COLLUSION_GROUP_PERCENT/)
  assert.match(collusionPageSource, /RESULT_LEFT_PANE_DEFAULT_PERCENT/)
  assert.equal(collusionPageSource.includes('w-[20%]'), false)
  assert.equal(collusionPageSource.includes('w-[22%]'), false)
})
