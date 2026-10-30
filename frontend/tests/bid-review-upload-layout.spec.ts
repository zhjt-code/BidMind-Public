import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const frontendSrc = join(dirname(fileURLToPath(import.meta.url)), '../src')

function readSrc(rel: string): string {
  return readFileSync(join(frontendSrc, rel), 'utf8')
}

test('投标审查上传页与串标/编写共用白卡片主题，不用卷宗纸纹', () => {
  const page = readSrc('pages/FileUpload.tsx')
  const view = readSrc('pages/fileUploadParts/BidReviewUploadView.tsx')
  const writing = readSrc('pages/writing/WritingUpload.tsx')
  assert.match(page, /BidReviewUploadView/)
  assert.match(page, /投标审查 — 上传文件/)
  assert.doesNotMatch(page, /upload-workbench/)
  assert.doesNotMatch(page, /bg-amber-50/)
  assert.doesNotMatch(view, /UploadDocket/)
  assert.match(view, /bg-white rounded-lg shadow-sm p-6/)
  assert.match(view, /SkipVlOption/)
  assert.match(view, /EnvelopeModeSwitch/)
  assert.match(writing, /bg-white rounded-lg shadow-sm p-6/)
  assert.doesNotMatch(writing, /rounded-xl border p-8/)
})

test('图片识别在卡片标题行右侧，三页同一文案常量', () => {
  const view = readSrc('pages/fileUploadParts/BidReviewUploadView.tsx')
  const writing = readSrc('pages/writing/WritingUpload.tsx')
  const collusion = readSrc('pages/CollusionUpload.tsx')

  const headerIdx = view.indexOf('mb-3 flex flex-wrap items-start justify-between gap-3')
  const skipIdx = view.indexOf('<SkipVlOption')
  const tenderIdx = view.indexOf('{tenderSlot}')
  assert.ok(headerIdx >= 0 && skipIdx > headerIdx && tenderIdx > skipIdx, '投标审查：图片识别必须在招标标题行内、投放区之前')

  const writingSkipIdx = writing.indexOf('id="skip-vl-writing"')
  const writingChooseIdx = writing.indexOf('点击选择文件')
  assert.ok(
    writingSkipIdx >= 0 && writingChooseIdx > writingSkipIdx,
    '标书编写：图片识别必须在点击选择文件之前',
  )

  const collusionTenderSkip = collusion.indexOf('id="skip-tender-vl"')
  const collusionTenderDrop = collusion.indexOf('id="tender-file"')
  assert.ok(
    collusionTenderSkip >= 0 && collusionTenderDrop > collusionTenderSkip,
    '串标招标：图片识别必须在投放区之前',
  )
  const collusionBidSkip = collusion.indexOf('id="skip-bid-vl"')
  const collusionBidHint = collusion.indexOf('将投标文件（PDF/Word）拖拽到此处')
  assert.ok(
    collusionBidSkip >= 0 && collusionBidHint > collusionBidSkip,
    '串标投标：图片识别必须在投放区之前',
  )
  const collusionBadgeIdx = collusion.indexOf('建议 · 1份')
  assert.ok(
    collusionBadgeIdx >= 0 && collusionTenderSkip > collusionBadgeIdx,
    '串标：建议徽章在标题侧，图片识别在右侧',
  )

  assert.match(view, /IMAGE_PACE_LABEL/)
  assert.match(writing, /IMAGE_PACE_LABEL/)
  assert.match(collusion, /IMAGE_PACE_LABEL/)
  assert.match(collusion, /BID_IMAGE_PACE_HINT/)
})

test('图片识别与信封构成共用分段控件和说明按钮，而非复选框提示卡', () => {
  const skipVl = readSrc('components/upload/SkipVlOption.tsx')
  const envelope = readSrc('pages/fileUploadParts/EnvelopeModeSwitch.tsx')
  const segmented = readSrc('components/upload/SegmentedControl.tsx')
  const hint = readSrc('components/upload/FieldHintButton.tsx')
  assert.match(skipVl, /documentPaceFromSkipVl/)
  assert.match(skipVl, /SegmentedControl/)
  assert.match(skipVl, /FieldHintButton/)
  assert.doesNotMatch(skipVl, /bg-amber-50/)
  assert.doesNotMatch(skipVl, /mt-0\.5 text-xs leading-relaxed text-muted-foreground/)
  assert.match(envelope, /SegmentedControl/)
  assert.match(envelope, /FieldHintButton/)
  assert.match(envelope, /ariaLabel="投标文件构成"/)
  assert.match(segmented, /role="radiogroup"/)
  assert.match(segmented, /role="radio"/)
  assert.match(hint, /aria-label=\{label\}/)
  assert.match(hint, /HelpCircle/)
  assert.doesNotMatch(hint, />\s*\?\s*</)
})

test('三页不把流程说明书做成彩色信息盒', () => {
  const view = readSrc('pages/fileUploadParts/BidReviewUploadView.tsx')
  const writing = readSrc('pages/writing/WritingUpload.tsx')
  const collusion = readSrc('pages/CollusionUpload.tsx')
  assert.match(view, /postProcessHint/)
  assert.doesNotMatch(view, /runModePanel/)
  assert.doesNotMatch(view, /RunModeOption/)
  assert.match(writing, /RunModeOption/)
  assert.doesNotMatch(collusion, /RunModeOption/)
  assert.doesNotMatch(view, /bg-blue-50 rounded-lg border border-blue-200/)
  assert.doesNotMatch(writing, /后续流程/)
  assert.doesNotMatch(collusion, /操作说明/)
  assert.match(collusion, /全部处理完后自动开始分析，通常需要 5–15 分钟/)
  assert.match(collusion, /建议 · 1份/)
  assert.match(collusion, /不上传也可分析。上传后系统能区分招标要求造成的正常一致与真实串通/)
  assert.doesNotMatch(collusion, /强烈建议上传招标文件/)
  assert.doesNotMatch(collusion, /flex items-start gap-2 mb-4 p-3 bg-blue-50/)
})
