import assert from 'node:assert/strict'
import test from 'node:test'

import {
  runReplaceFailedUpload,
  type ReplaceUploadFile,
  type ReplaceUploadFileStatus,
} from '../src/pages/fileUploadParts/runReplaceFailedUpload.ts'

const ALLOWED_STATUSES: ReplaceUploadFileStatus[] = [
  'pending',
  'uploading',
  'processing',
  'success',
  'error',
]

test('更换上传 setFileStatus 只写入字面量 status，可与页面 EnvelopeUploadedFile 对齐', async () => {
  const statuses: string[] = []
  let current: ReplaceUploadFile | null = {
    file: new File(['x'], 'tender.pdf', { type: 'application/pdf' }),
    name: 'tender.pdf',
    status: 'pending',
  }

  const result = await runReplaceFailedUpload({
    projectId: 1,
    slot: { id: 'tender', docType: 'tender', bidPart: null },
    file: current,
    skipVlForTender: true,
    uploadFile: async () => ({ data: { id: 101 } }),
    processDocument: async () => undefined,
    getDocuments: async () => ({
      data: {
        tender: {
          id: 101,
          filename: 'tender.pdf',
          file_size: 1,
          created_at: '2026-08-12T00:00:00Z',
          parsed: 1,
        },
        bids: [
          {
            id: 102,
            filename: 'bid.pdf',
            file_size: 1,
            created_at: '2026-08-12T00:00:00Z',
            parsed: 1,
            bid_part: null,
          },
        ],
      },
    }),
    getProjectStatus: async () => 'created',
    startPolling: async () => undefined,
    setFileStatus: (updater) => {
      current = updater(current)
      if (current) statuses.push(current.status)
    },
  })

  assert.deepEqual(statuses, ['uploading', 'processing'])
  for (const status of statuses) {
    assert.ok(
      (ALLOWED_STATUSES as string[]).includes(status),
      `status ${status} must stay in ReplaceUploadFileStatus union`,
    )
  }
  assert.equal(result.tender?.id, 101)
  assert.equal(result.bids.length, 1)
  assert.equal(result.bids[0].id, 102)
})

test('分卷单卷更换: 上传携带 bid_part, bids 缺失时回退单文档字段', async () => {
  const uploadArgs: unknown[] = []
  let current: ReplaceUploadFile | null = {
    file: new File(['x'], 'technical.pdf', { type: 'application/pdf' }),
    name: 'technical.pdf',
    status: 'pending',
  }

  const result = await runReplaceFailedUpload({
    projectId: 1,
    slot: { id: 'technical', docType: 'bid', bidPart: 'technical' },
    file: current,
    skipVlForTender: true,
    uploadFile: async (pid, file, docType, bidPart) => {
      uploadArgs.push([pid, docType, bidPart])
      return { data: { id: 202 } }
    },
    processDocument: async (_pid, _docId, skipVl) => {
      // 投标文件不跳过图片识别
      assert.equal(skipVl, false)
      return undefined
    },
    getDocuments: async () => ({
      data: {
        tender: {
          id: 101,
          filename: 'tender.pdf',
          file_size: 1,
          created_at: '2026-08-12T00:00:00Z',
          parsed: 1,
        },
        bid: {
          id: 202,
          filename: 'technical.pdf',
          file_size: 1,
          created_at: '2026-08-12T00:00:00Z',
          parsed: 1,
          bid_part: 'technical',
        },
      },
    }),
    getProjectStatus: async () => 'created',
    startPolling: async (slotId) => {
      assert.equal(slotId, 'technical')
      return undefined
    },
    setFileStatus: (updater) => {
      current = updater(current)
    },
  })

  assert.deepEqual(uploadArgs, [[1, 'bid', 'technical']])
  assert.equal(result.bids.length, 1)
  assert.equal(result.bids[0].bid_part, 'technical')
})
