import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('目录生成完成态应跟随后端 steps 而非前端强制打满', () => {
  const outlinePage = readFileSync('frontend/src/pages/writing/WritingOutline.tsx', 'utf8')

  assert.doesNotMatch(
    outlinePage,
    /mapped\.steps\.map\(\(s\) => \(\{ \.\.\.s, status: 'completed' as const, targetProgress: 1 \}\)\)/,
  )
  assert.match(outlinePage, /updateTargets\(mapped\.steps, mapped\.overallTarget\)/)
})

test('目录画像阶段不应在前端叠加时间推演 fakeProgress', () => {
  const outlinePage = readFileSync('frontend/src/pages/writing/WritingOutline.tsx', 'utf8')

  assert.doesNotMatch(outlinePage, /fakeProgress/)
  assert.doesNotMatch(outlinePage, /profilingTickRef/)
  assert.doesNotMatch(outlinePage, /targetProgress < fakeProgress/)
})
