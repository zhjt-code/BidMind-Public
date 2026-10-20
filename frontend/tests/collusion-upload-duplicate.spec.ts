/**
 * collusionUploadParts/duplicate.ts 纯函数边界测试。
 *
 * 抽自 CollusionUpload.tsx 的 checkDuplicateFiles useCallback；
 * 按 R2-9 §1.2 决议（Q2=B）不抽 hook 仅按纯函数 + 子组件拆分。
 *
 * 覆盖 5 条核心 + 2 条边界：
 *  - 单批无重名
 *  - 跨已有列表重名
 *  - 同批内自身重名
 *  - 全部重名
 *  - 空数组边界
 *  - 同批内多次重名仅去重出现一次
 *  - existingNames 为空时仅同批比对
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import { checkDuplicateFiles } from '../src/pages/collusionUploadParts/duplicate.ts'

function makeFile(name: string): File {
  // node:test 环境无原生 File 构造，使用最小桩
  return { name } as unknown as File
}

test('checkDuplicateFiles: 单批无重名 返回空 duplicateNames', () => {
  const files = [makeFile('a.pdf'), makeFile('b.pdf'), makeFile('c.pdf')]
  const result = checkDuplicateFiles(files, ['x.pdf', 'y.pdf'])
  assert.deepEqual(result.duplicateNames, [])
  assert.equal(result.filesToAdd.length, 3)
  assert.equal(result.filesToAdd[0], files[0])
})

test('checkDuplicateFiles: 跨已有列表重名 命中 existingNames', () => {
  const files = [makeFile('a.pdf'), makeFile('existing1.pdf'), makeFile('c.pdf')]
  const result = checkDuplicateFiles(files, ['existing1.pdf', 'existing2.pdf'])
  assert.deepEqual(result.duplicateNames, ['existing1.pdf'])
  assert.equal(result.filesToAdd.length, 3)
})

test('checkDuplicateFiles: 同批内自身重名 第二次出现进入 duplicateNames', () => {
  const files = [makeFile('a.pdf'), makeFile('dup.pdf'), makeFile('dup.pdf')]
  const result = checkDuplicateFiles(files, [])
  assert.deepEqual(result.duplicateNames, ['dup.pdf'])
})

test('checkDuplicateFiles: 全部重名 全部进入 duplicateNames(去重)', () => {
  const files = [makeFile('x.pdf'), makeFile('y.pdf')]
  const result = checkDuplicateFiles(files, ['x.pdf', 'y.pdf', 'z.pdf'])
  assert.deepEqual(result.duplicateNames, ['x.pdf', 'y.pdf'])
})

test('checkDuplicateFiles: 空 newFiles 返回空数组', () => {
  const result = checkDuplicateFiles([], ['a.pdf', 'b.pdf'])
  assert.deepEqual(result.duplicateNames, [])
  assert.deepEqual(result.filesToAdd, [])
})

test('checkDuplicateFiles: 同名出现 3 次 duplicateNames 只记录一次', () => {
  const files = [
    makeFile('repeat.pdf'),
    makeFile('repeat.pdf'),
    makeFile('repeat.pdf'),
    makeFile('other.pdf'),
  ]
  const result = checkDuplicateFiles(files, [])
  assert.deepEqual(result.duplicateNames, ['repeat.pdf'])
})

test('checkDuplicateFiles: existingNames 为空 时仅同批内比对', () => {
  const files = [makeFile('a.pdf'), makeFile('a.pdf'), makeFile('b.pdf')]
  const result = checkDuplicateFiles(files, [])
  assert.deepEqual(result.duplicateNames, ['a.pdf'])
  assert.equal(result.filesToAdd.length, 3)
})
