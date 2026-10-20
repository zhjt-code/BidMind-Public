import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  canAutoUseKnowledgeDocument,
  formatKnowledgeFileSize,
  getDefaultBusinessTypeForCategory,
  getDefaultFlowsForKnowledgeType,
  getKnowledgeFileKey,
  getKnowledgeBusinessTypeLabel,
  getKnowledgeLibraryCategory,
  getKnowledgeUnavailableReason,
  isSupportedKnowledgeFileName,
  isKnowledgeDocumentExpired,
  validateKnowledgeFile,
} from '../src/services/knowledgeBaseView.ts'

const knowledgeBasePageSource = readFileSync(
  new URL('../src/pages/KnowledgeBase.tsx', import.meta.url),
  'utf8',
)

const NOW = new Date('2026-05-08T12:00:00+08:00')

test('知识库资料过期判断基于 validTo', () => {
  assert.equal(isKnowledgeDocumentExpired({ validTo: '2026-05-07' }, NOW), true)
  assert.equal(isKnowledgeDocumentExpired({ validTo: '2026-05-08' }, NOW), false)
  assert.equal(isKnowledgeDocumentExpired({ validTo: '2026-05-09' }, NOW), false)
  assert.equal(isKnowledgeDocumentExpired({ validTo: null }, NOW), false)
})

test('自动使用知识库资料必须启用、审核通过、完成索引且未过期', () => {
  assert.equal(
    canAutoUseKnowledgeDocument({
      enabled: true,
      reviewStatus: 'approved',
      processingStatus: 'completed',
      validTo: '2026-05-09',
    }, NOW),
    true,
  )
  assert.equal(
    canAutoUseKnowledgeDocument({
      enabled: true,
      reviewStatus: 'approved',
      processingStatus: 'completed',
      validTo: '2026-05-07',
    }, NOW),
    false,
  )
  assert.equal(
    canAutoUseKnowledgeDocument({
      enabled: false,
      reviewStatus: 'approved',
      processingStatus: 'completed',
      validTo: '2026-05-09',
    }, NOW),
    false,
  )
  assert.equal(
    canAutoUseKnowledgeDocument({
      enabled: true,
      reviewStatus: 'pending',
      processingStatus: 'completed',
      validTo: '2026-05-09',
    }, NOW),
    false,
  )
  assert.equal(
    canAutoUseKnowledgeDocument({
      enabled: true,
      reviewStatus: 'approved',
      processingStatus: 'failed',
      validTo: '2026-05-09',
    }, NOW),
    false,
  )
})

test('知识库资料不可用原因可解释治理状态', () => {
  assert.equal(getKnowledgeUnavailableReason({
    enabled: false,
    reviewStatus: 'approved',
    processingStatus: 'completed',
    validTo: '2026-05-09',
  }, NOW), '已停用')
  assert.equal(getKnowledgeUnavailableReason({
    enabled: true,
    reviewStatus: 'approved',
    processingStatus: 'completed',
    validTo: '2026-05-07',
  }, NOW), '已过期')
  assert.equal(getKnowledgeUnavailableReason({
    enabled: true,
    reviewStatus: 'approved',
    processingStatus: 'completed',
    validTo: '2026-05-09',
  }, NOW), null)
})

test('知识库业务类型映射为中文并归类到三类资料库', () => {
  assert.equal(getKnowledgeBusinessTypeLabel('company_profile'), '公司简介')
  assert.equal(getKnowledgeBusinessTypeLabel('normal_similarity'), '串标白名单')
  assert.equal(getKnowledgeLibraryCategory({ businessType: 'company_profile' }), 'enterprise')
  assert.equal(getKnowledgeLibraryCategory({ businessType: 'standard' }), 'standard')
  assert.equal(getKnowledgeLibraryCategory({ businessType: 'normal_similarity' }), 'experience')
  assert.equal(getDefaultBusinessTypeForCategory('enterprise'), 'company_profile')
  assert.deepEqual(getDefaultFlowsForKnowledgeType('standard'), ['bid_writing', 'bid_review', 'collusion_check'])
  assert.deepEqual(getDefaultFlowsForKnowledgeType('normal_similarity'), ['collusion_check'])
})

test('知识库上传文件大小格式化', () => {
  assert.equal(formatKnowledgeFileSize(0), '0 B')
  assert.equal(formatKnowledgeFileSize(1024), '1.00 KB')
  assert.equal(formatKnowledgeFileSize(2.5 * 1024 * 1024), '2.50 MB')
  assert.equal(formatKnowledgeFileSize(12 * 1024 * 1024), '12.0 MB')
})

test('知识库上传文件扩展名与大小校验', () => {
  assert.equal(isSupportedKnowledgeFileName('标准.PDF'), true)
  assert.equal(isSupportedKnowledgeFileName('资料.docx'), true)
  assert.equal(isSupportedKnowledgeFileName('营业执照.jpg'), true)
  assert.equal(isSupportedKnowledgeFileName('资质证书.PNG'), true)
  assert.equal(isSupportedKnowledgeFileName('现场照片.webp'), true)
  assert.equal(isSupportedKnowledgeFileName('脚本.exe'), false)
  assert.equal(validateKnowledgeFile({ name: '资料.exe', size: 10 }), '不支持的文件格式: 资料.exe')
  assert.equal(validateKnowledgeFile({ name: '边界.pdf', size: 200 * 1024 * 1024 }), null)
  assert.equal(validateKnowledgeFile({ name: '大文件.pdf', size: 201 * 1024 * 1024 }), '文件超过200MB: 大文件.pdf')
  assert.equal(validateKnowledgeFile({ name: '规范.pdf', size: 1024 }), null)
})

test('知识库上传文件去重 key 包含名称大小和修改时间', () => {
  assert.equal(
    getKnowledgeFileKey({ name: '规范.pdf', size: 1024, lastModified: 1 }),
    '规范.pdf|1024|1',
  )
  assert.notEqual(
    getKnowledgeFileKey({ name: '规范.pdf', size: 1024, lastModified: 1 }),
    getKnowledgeFileKey({ name: '规范.pdf', size: 1024, lastModified: 2 }),
  )
})

test('知识库资料列表仅保留粒度主操作，次要操作折叠到更多菜单', () => {
  assert.match(knowledgeBasePageSource, /DropdownMenu/)
  assert.match(knowledgeBasePageSource, /更多操作/)
  assert.match(knowledgeBasePageSource, /<Button[^>]*onClick=\{\(\) => toggleAtoms\(doc\)\}/)
  assert.match(knowledgeBasePageSource, /DropdownMenuTrigger/)
  assert.match(knowledgeBasePageSource, /DropdownMenuItem[\s\S]*?onClick=\{\(\) => handleProcess\(doc\)\}/)
  assert.match(knowledgeBasePageSource, /DropdownMenuItem[\s\S]*?onClick=\{\(\) => openEditDialog\(doc\)\}/)
  assert.match(knowledgeBasePageSource, /DropdownMenuItem[\s\S]*?onClick=\{\(\) => handleToggleEnabled\(doc\)\}/)
  assert.match(knowledgeBasePageSource, /DropdownMenuItem[\s\S]*?onClick=\{\(\) => handleDeleteDocument\(doc\)\}/)
  assert.doesNotMatch(knowledgeBasePageSource, /<Button[^>]*onClick=\{\(\) => handleProcess\(doc\)\}/)
  assert.doesNotMatch(knowledgeBasePageSource, /<Button[^>]*onClick=\{\(\) => openEditDialog\(doc\)\}/)
  assert.doesNotMatch(knowledgeBasePageSource, /<Button[^>]*onClick=\{\(\) => handleToggleEnabled\(doc\)\}/)
  assert.doesNotMatch(knowledgeBasePageSource, /<Button[^>]*onClick=\{\(\) => handleDeleteDocument\(doc\)\}/)
})
