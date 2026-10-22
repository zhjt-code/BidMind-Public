import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  buildComposeTree,
  draftContentHasGeneratedDiagram,
} from '../src/pages/writing/writingComposeTree.ts'

test('draftContentHasGeneratedDiagram: 空内容不算生成图示', () => {
  assert.equal(draftContentHasGeneratedDiagram(null), false)
  assert.equal(draftContentHasGeneratedDiagram(undefined), false)
  assert.equal(draftContentHasGeneratedDiagram(''), false)
  assert.equal(draftContentHasGeneratedDiagram('<p>无图正文</p>'), false)
})

test('draftContentHasGeneratedDiagram: 仅认 figure 上的数字 data-diagram-id', () => {
  assert.equal(
    draftContentHasGeneratedDiagram(
      '<p>见下图</p><figure data-diagram-id="12"><figcaption>实施流程</figcaption></figure>',
    ),
    true,
  )
  assert.equal(
    draftContentHasGeneratedDiagram(
      '<figure class="x" data-diagram-id="8"><figcaption>进度</figcaption></figure>',
    ),
    true,
  )
})

test('draftContentHasGeneratedDiagram: 素材图与非数字占位不算', () => {
  assert.equal(
    draftContentHasGeneratedDiagram(
      '<figure data-image-id="9"><figcaption>营业执照</figcaption></figure>',
    ),
    false,
  )
  assert.equal(
    draftContentHasGeneratedDiagram('<img src="/x.png" alt="扫描件" />'),
    false,
  )
  assert.equal(
    draftContentHasGeneratedDiagram(
      '<figure data-diagram-id="gantt-90day"><figcaption>占位</figcaption></figure>',
    ),
    false,
  )
})

test('buildComposeTree: 叶子按草稿 HTML 标记生成图示，父节点不标记', () => {
  const { treeRoots } = buildComposeTree(
    [
      { id: 1, parentId: null, title: '技术方案', depth: 0 },
      { id: 2, parentId: 1, title: '实施流程', depth: 1 },
      { id: 3, parentId: 1, title: '营业执照', depth: 1 },
    ],
    [
      {
        outlineNodeId: 2,
        status: 'draft',
        content: '<figure data-diagram-id="21"><figcaption>流程</figcaption></figure>',
      },
      {
        outlineNodeId: 3,
        status: 'user_edited',
        content: '<figure data-image-id="3"><figcaption>证件</figcaption></figure>',
      },
    ],
  )

  assert.equal(treeRoots.length, 1)
  assert.equal(treeRoots[0].hasGeneratedDiagram, false)
  assert.equal(treeRoots[0].draftStatus, null)
  const [flow, license] = treeRoots[0].children
  assert.equal(flow.title, '实施流程')
  assert.equal(flow.hasGeneratedDiagram, true)
  assert.equal(flow.draftStatus, 'draft')
  assert.equal(license.title, '营业执照')
  assert.equal(license.hasGeneratedDiagram, false)
  assert.equal(license.draftStatus, 'user_edited')
})

test('侧栏状态点填色不变，有生成图示时仅加橙色色环', () => {
  const source = readFileSync(
    new URL('../src/pages/writing/WritingComposeSidebar.tsx', import.meta.url),
    'utf8',
  )

  assert.match(source, /draft:\s*'bg-blue-400'/)
  assert.match(source, /generating:\s*'bg-amber-400 animate-pulse'/)
  assert.match(source, /user_edited:\s*'bg-green-400'/)
  assert.match(source, /hasGeneratedDiagram/)
  assert.match(source, /ring-2 ring-orange-500 ring-offset-1 ring-offset-white/)
  assert.match(source, /data-has-generated-diagram/)
  assert.doesNotMatch(source, /STATUS_DOT\[.*hasGenerated/)
})
