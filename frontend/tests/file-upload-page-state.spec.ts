import assert from 'node:assert/strict'
import test from 'node:test'

import {
  deriveBidReviewUploadPageState,
  isBidReviewUploadChooserMode,
} from '../src/pages/fileUploadParts/uploadPageState.ts'

const parsedTender = { exists: true, parsed: 1 }
const missingTender = { exists: false, parsed: null }

// 投标侧信封聚合态: 整本已解析 / 无文档 / 分卷缺卷 / 分卷齐套未解析完
const parsedBidEnvelope = { anyDoc: true, complete: true, allParsed: true }
const missingBidEnvelope = { anyDoc: false, complete: false, allParsed: false }
const partialBidEnvelope = { anyDoc: true, complete: false, allParsed: false }
const unparsedBidEnvelope = { anyDoc: true, complete: true, allParsed: false }

test('uploading 半成品缺投标文件时进入恢复模式而不是普通上传', () => {
  const state = deriveBidReviewUploadPageState({
    projectStatus: 'uploading',
    tender: parsedTender,
    bid: missingBidEnvelope,
    isUploading: false,
    isSomethingLoading: false,
  })

  assert.equal(state.mode, 'recover_half_upload')
  assert.equal(state.canShowUploadInputs, false)
})

test('当前上传过程中单份文件先落库时保持处理中而不是恢复模式', () => {
  const state = deriveBidReviewUploadPageState({
    projectStatus: 'created',
    tender: { exists: true, parsed: 0 },
    bid: missingBidEnvelope,
    isUploading: true,
    isSomethingLoading: true,
  })

  assert.equal(state.mode, 'processing')
  assert.equal(state.canShowUploadInputs, false)
})

test('created 状态已有单份文档也视为半成品，要求重新开始上传', () => {
  const state = deriveBidReviewUploadPageState({
    projectStatus: 'created',
    tender: parsedTender,
    bid: missingBidEnvelope,
    isUploading: false,
    isSomethingLoading: false,
  })

  assert.equal(state.mode, 'recover_half_upload')
  assert.equal(state.canShowUploadInputs, false)
})

test('分卷只传商务标(缺技术标)视为半成品(信封未齐套)', () => {
  const state = deriveBidReviewUploadPageState({
    projectStatus: 'created',
    tender: parsedTender,
    bid: partialBidEnvelope,
    isUploading: false,
    isSomethingLoading: false,
  })

  assert.equal(state.mode, 'recover_half_upload')
  assert.equal(state.canShowUploadInputs, false)
})

test('parsed 就绪态直接进入要求页而不是上传页', () => {
  const state = deriveBidReviewUploadPageState({
    projectStatus: 'parsed',
    tender: parsedTender,
    bid: parsedBidEnvelope,
    isUploading: false,
    isSomethingLoading: false,
  })

  assert.equal(state.mode, 'redirect')
  assert.equal(state.targetRoute, 'requirements')
})

test('parsed + express 进入进度页而不是要求确认页', () => {
  const state = deriveBidReviewUploadPageState({
    projectStatus: 'parsed',
    tender: parsedTender,
    bid: parsedBidEnvelope,
    isUploading: false,
    isSomethingLoading: false,
    runMode: 'express',
  })

  assert.equal(state.mode, 'redirect')
  assert.equal(state.targetRoute, 'progress')
})

test('全部文档解析完成且未进入审查时跳转要求确认页', () => {
  const state = deriveBidReviewUploadPageState({
    projectStatus: 'created',
    tender: parsedTender,
    bid: parsedBidEnvelope,
    isUploading: false,
    isSomethingLoading: false,
  })

  assert.equal(state.mode, 'redirect')
  assert.equal(state.targetRoute, 'requirements')
})

test('信封齐套但仍有卷未解析完时保持处理中', () => {
  const state = deriveBidReviewUploadPageState({
    projectStatus: 'created',
    tender: parsedTender,
    bid: unparsedBidEnvelope,
    isUploading: false,
    isSomethingLoading: true,
  })

  assert.equal(state.mode, 'processing')
  assert.equal(state.canShowUploadInputs, false)
})

test('审查中项目直接访问上传页时跳转进度页', () => {
  const state = deriveBidReviewUploadPageState({
    projectStatus: 'reviewing',
    tender: parsedTender,
    bid: parsedBidEnvelope,
    isUploading: false,
    isSomethingLoading: false,
  })

  assert.equal(state.mode, 'redirect')
  assert.equal(state.targetRoute, 'progress')
})

test('新项目无文档时允许显示普通上传入口', () => {
  const state = deriveBidReviewUploadPageState({
    projectStatus: 'created',
    tender: missingTender,
    bid: missingBidEnvelope,
    isUploading: false,
    isSomethingLoading: false,
  })

  assert.equal(state.mode, 'upload')
  assert.equal(state.canShowUploadInputs, true)
})

test('单侧更换时即使其余槽位已存在也不进入半成品恢复态', () => {
  const state = deriveBidReviewUploadPageState({
    projectStatus: 'failed',
    tender: { exists: true, parsed: -1 },
    bid: parsedBidEnvelope,
    isUploading: false,
    isSomethingLoading: false,
    replacingSlot: 'tender',
  })

  assert.equal(state.mode, 'replacing')
  assert.equal(state.canShowUploadInputs, true)
})

test('分卷单卷更换(槽位 id 为卷名)同样不进入半成品恢复态', () => {
  const state = deriveBidReviewUploadPageState({
    projectStatus: 'failed',
    tender: parsedTender,
    bid: partialBidEnvelope,
    isUploading: false,
    isSomethingLoading: false,
    replacingSlot: 'technical',
  })

  assert.equal(state.mode, 'replacing')
  assert.equal(state.canShowUploadInputs, true)
})

test('服务端真正缺一侧时仍进入半成品恢复态', () => {
  const state = deriveBidReviewUploadPageState({
    projectStatus: 'failed',
    tender: missingTender,
    bid: parsedBidEnvelope,
    isUploading: false,
    isSomethingLoading: false,
    replacingSlot: null,
  })

  assert.equal(state.mode, 'recover_half_upload')
  assert.equal(state.canShowUploadInputs, false)
})

test('审标处理进度与更换态不是上传选择态，推进方式应隐藏', () => {
  const processing = deriveBidReviewUploadPageState({
    projectStatus: 'created',
    tender: { exists: true, parsed: 0 },
    bid: missingBidEnvelope,
    isUploading: true,
    isSomethingLoading: true,
  })
  const idle = deriveBidReviewUploadPageState({
    projectStatus: 'created',
    tender: missingTender,
    bid: missingBidEnvelope,
    isUploading: false,
    isSomethingLoading: false,
  })

  assert.equal(processing.mode, 'processing')
  assert.equal(isBidReviewUploadChooserMode(processing.mode), false)
  assert.equal(idle.mode, 'upload')
  assert.equal(isBidReviewUploadChooserMode(idle.mode), true)
  assert.equal(isBidReviewUploadChooserMode('replacing'), false)
})
