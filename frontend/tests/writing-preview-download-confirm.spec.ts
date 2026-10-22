import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  splitIssuesBySeverity,
  deriveBannerClassName,
  deriveDownloadButtonLabel,
  computeDownloadSeverityPreview,
  derivePreviewActionAvailability,
  summarizeScoringResponse,
  shouldAllowBlockingIssuesOnConfirmedDownload,
  BANNER_CLASS_HIGH,
  BANNER_CLASS_NORMAL,
  DOWNLOAD_BUTTON_LABEL_HIGH,
  DOWNLOAD_BUTTON_LABEL_NORMAL,
  DOCX_TOC_GUIDE_TEXT,
  DOCX_UNLOCK_GUIDE_TEXT,
  PREVIEW_BLOCKED_REASON_FAILED,
} from '../src/pages/writing/writingPreviewDownloadHelpers.ts'

test('splitIssuesBySeverity 在空/undefined 时返回空数组', () => {
  assert.deepEqual(splitIssuesBySeverity(undefined), { high: [], other: [], info: [] })
  assert.deepEqual(splitIssuesBySeverity(null), { high: [], other: [], info: [] })
  assert.deepEqual(splitIssuesBySeverity([]), { high: [], other: [], info: [] })
})

test('splitIssuesBySeverity 按 severity 字段拆分，info 单列', () => {
  const issues = [
    { severity: 'high', message: 'A' },
    { severity: 'low', message: 'B' },
    { severity: 'medium', message: 'C' },
    { severity: 'high', message: 'D' },
    { severity: 'info', message: 'E' },
  ]
  const { high, other, info } = splitIssuesBySeverity(issues)
  assert.equal(high.length, 2)
  assert.equal(other.length, 2) // low + medium，info 不计入
  assert.equal(info.length, 1)
  assert.equal(high[0].message, 'A')
  assert.equal(high[1].message, 'D')
  assert.equal(info[0].message, 'E')
})

test('banner 色随 highSeverity 数量切换', () => {
  assert.equal(deriveBannerClassName(0), BANNER_CLASS_NORMAL)
  assert.equal(deriveBannerClassName(1), BANNER_CLASS_HIGH)
  assert.equal(deriveBannerClassName(5), BANNER_CLASS_HIGH)
})

test('下载按钮文案随 highSeverity 数量切换', () => {
  assert.equal(deriveDownloadButtonLabel(0), DOWNLOAD_BUTTON_LABEL_NORMAL)
  assert.equal(deriveDownloadButtonLabel(3), DOWNLOAD_BUTTON_LABEL_HIGH)
})

test('确认下载仅在存在严重问题时携带后端覆盖意图', () => {
  assert.equal(shouldAllowBlockingIssuesOnConfirmedDownload(0), false)
  assert.equal(shouldAllowBlockingIssuesOnConfirmedDownload(1), true)
  assert.equal(shouldAllowBlockingIssuesOnConfirmedDownload(5), true)
})

test('下载弹窗 severity 预览限制 3 条并携带溢出计数', () => {
  const issues = [
    { severity: 'high', message: '1' },
    { severity: 'high', message: '2' },
    { severity: 'high', message: '3' },
    { severity: 'high', message: '4' },
    { severity: 'high', message: '5' },
  ]
  const { preview, overflowCount } = computeDownloadSeverityPreview(issues)
  assert.equal(preview.length, 3)
  assert.equal(overflowCount, 2)
  assert.equal(preview[0].message, '1')
})

test('下载弹窗 severity 预览无溢出时 overflowCount=0', () => {
  const issues = [
    { severity: 'high', message: '1' },
    { severity: 'high', message: '2' },
  ]
  const { preview, overflowCount } = computeDownloadSeverityPreview(issues)
  assert.equal(preview.length, 2)
  assert.equal(overflowCount, 0)
})

test('下载弹窗 severity 预览空 issues 时返回空', () => {
  const { preview, overflowCount } = computeDownloadSeverityPreview([])
  assert.equal(preview.length, 0)
  assert.equal(overflowCount, 0)
})

test('评分项摘要统计 weak_support，避免 high 修复后评分项检查消失', () => {
  const summary = summarizeScoringResponse([
    {
      type: 'scoring_weak_support',
      severity: 'medium',
      message: "评分项'人员素质'（满分10分）对应章节缺素材",
    },
    {
      type: 'clause_coverage',
      severity: 'warning',
      message: '普通约束覆盖提醒',
    },
  ])

  assert.equal(summary.totalIssueCount, 1)
  assert.equal(summary.weakSupportCount, 1)
  assert.equal(summary.unrespondedCount, 0)
  assert.equal(summary.unrespondedScore, 0)
})

test('评分项摘要保留未响应分值统计', () => {
  const summary = summarizeScoringResponse([
    {
      type: 'scoring_unresponded',
      severity: 'high',
      message: "评分项'工期保障'（满分6分）未被任何章节响应",
    },
  ])

  assert.equal(summary.totalIssueCount, 1)
  assert.equal(summary.unrespondedCount, 1)
  assert.equal(summary.unrespondedScore, 6)
})

test('failed 状态应禁用预览页的渲染与下载入口（防 复发）', () => {
  const result = derivePreviewActionAvailability('failed')
  assert.equal(result.canRender, false)
  assert.equal(result.canDownload, false)
  assert.equal(result.blockedReason, PREVIEW_BLOCKED_REASON_FAILED)
})

test('composed/formatting/completed 等可预览态应放行渲染与下载入口', () => {
  for (const status of ['composed', 'formatting', 'completed']) {
    const result = derivePreviewActionAvailability(status)
    assert.equal(result.canRender, true, `${status} 应允许渲染`)
    assert.equal(result.canDownload, true, `${status} 应允许下载`)
    assert.equal(result.blockedReason, null, `${status} 不应有阻断文案`)
  }
})

test('未知/空状态默认放行（兜底由后端 _ensure_preview_ready 校验）', () => {
  for (const status of [null, undefined, '', 'writing', 'unknown']) {
    const result = derivePreviewActionAvailability(status as string | null)
    assert.equal(result.canRender, true, `${String(status)} 不应在前端硬阻断`)
    assert.equal(result.canDownload, true, `${String(status)} 不应在前端硬阻断`)
    assert.equal(result.blockedReason, null)
  }
})

test('WritingPreview 一致性问题展示应接入用户态清洗', () => {
  const source = readFileSync(new URL('../src/pages/writing/WritingPreview.tsx', import.meta.url), 'utf8')

  assert.match(source, /sanitizeUserFacingText/)
  assert.match(source, /formatConsistencyIssueMessage/)
  assert.match(source, /formatConsistencyIssueDetail/)
})

test('WritingPreview 检查结果横幅默认折叠，仅保留摘要入口', () => {
  const source = readFileSync(new URL('../src/pages/writing/WritingPreview.tsx', import.meta.url), 'utf8')

  assert.match(source, /const \[showIssues, setShowIssues\] = useState\(false\)/)
  assert.match(source, /data-testid="consistency-banner"/)
  assert.match(source, /onClick=\{\(\) => setShowIssues\(!showIssues\)\}/)
  assert.match(source, /aria-expanded=\{showIssues\}/)
  assert.match(source, /\{showIssues && \(/)
})

test('下载确认引导文案应说明用 Word 自动目录而非系统预置半成品目录', () => {
  assert.match(DOCX_TOC_GUIDE_TEXT, /引用/)
  assert.match(DOCX_TOC_GUIDE_TEXT, /自动目录/)
  assert.doesNotMatch(DOCX_TOC_GUIDE_TEXT, /右键更新域/)

  const source = readFileSync(new URL('../src/pages/writing/WritingPreview.tsx', import.meta.url), 'utf8')
  assert.match(source, /DOCX_TOC_GUIDE_TEXT/)
  assert.match(source, /各章节正文及素材引用内容/)
  assert.doesNotMatch(source, /文档包含目录结构、各章节正文/)
})

test('解除锁定提示应固定出现在下载确认弹窗，而非仅首次 toast', () => {
  assert.match(DOCX_UNLOCK_GUIDE_TEXT, /解除锁定/)
  assert.match(DOCX_UNLOCK_GUIDE_TEXT, /属性/)

  const source = readFileSync(new URL('../src/pages/writing/WritingPreview.tsx', import.meta.url), 'utf8')
  assert.match(source, /DOCX_UNLOCK_GUIDE_TEXT/)
  assert.match(source, /data-testid="download-open-tips"/)
  assert.doesNotMatch(source, /docx_download_tip_shown/)
  assert.doesNotMatch(source, /localStorage\.getItem\(TIP_KEY\)/)
})

test('下载确认弹窗必须限制在视口内，严重问题区可局部滚动且底部操作可达', () => {
  const source = readFileSync(new URL('../src/pages/writing/WritingPreview.tsx', import.meta.url), 'utf8')
  assert.match(source, /showDownloadConfirm[\s\S]*max-h-\[calc\(100vh-2rem\)\]/)
  assert.match(source, /showDownloadConfirm[\s\S]*flex flex-col overflow-hidden/)
  assert.match(source, /flex-1 min-h-0 overflow-y-auto overscroll-contain/)
  assert.match(source, /data-testid="download-severity-warning"[\s\S]*max-h-\[28vh\] overflow-y-auto/)
  assert.match(source, /DialogFooter className="shrink-0"/)
})
