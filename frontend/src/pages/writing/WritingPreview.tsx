import React, { useEffect, useMemo, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { ArrowLeft, Download, AlertTriangle, RefreshCw, Loader2, ChevronDown } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { useFirstVisitDialog } from '@/hooks/useFirstVisitDialog'
import { writingService } from '@/services/writingApi'
import type { WritingRetryState } from '@/services/writingRetryTransforms'
import { sanitizeUserFacingText } from '@/services/progressStatusTransforms'
import ProjectTimelineButton from '@/components/ProjectTimelineButton'
import { buildExportFilename, downloadBlob } from '@/lib/download'
import { useCachedProjectName } from '@/hooks/useCachedProjectName'
import { useWritingStore } from '@/stores'
import { toast } from 'sonner'
import { useWritingGuard } from './useWritingGuard'
import { isAtLeast } from './writingConstants.ts'
import {
  getReadableErrorMessage,
  isStatusBlockedMessage,
} from './writingPreviewFeedback'
import {
  splitIssuesBySeverity,
  summarizeScoringResponse,
  deriveBannerClassName,
  deriveDownloadButtonLabel,
  computeDownloadSeverityPreview,
  derivePreviewActionAvailability,
  shouldAllowBlockingIssuesOnConfirmedDownload,
  DOCX_TOC_GUIDE_TEXT,
  DOCX_UNLOCK_GUIDE_TEXT,
} from './writingPreviewDownloadHelpers'
import {
  buildConsistencyIssueGroups,
  getConsistencyIssueGroupStats,
} from './writingConsistencyGrouping'
import WritingStepBar from './WritingStepBar'
import RetryActionPanel from './RetryActionPanel'

function formatConsistencyIssueMessage(issue: { message?: string | null }): string {
  return sanitizeUserFacingText(issue.message) || '检查项需要复核'
}

function formatConsistencyIssueDetail(issue: { detail?: string | null }): string {
  return sanitizeUserFacingText(issue.detail)
}

const WritingPreviewPage: React.FC = () => {
  const { id: projectId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const pid = Number(projectId)
  const projectName = useCachedProjectName(pid)
  const { ready, loading: guardLoading, projectStatus } = useWritingGuard(pid, 'composed')
  const { consistencyIssues, loadConsistency } = useWritingStore()
  const [previewHtml, setPreviewHtml] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [rendering, setRendering] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [showDownloadConfirm, setShowDownloadConfirm] = useState(false)
  const [showIssues, setShowIssues] = useState(false)
  const [collapsedIssueGroups, setCollapsedIssueGroups] = useState<Record<string, boolean>>({})
  const [retryState, setRetryState] = useState<WritingRetryState | null>(null)
  const [refreshingRetry, setRefreshingRetry] = useState(false)
  const canLoadPreviewData = ready && !guardLoading && isAtLeast(projectStatus, 'composed')

  const showRequestFeedback = useCallback((message: string, fallback: string) => {
    const readableMessage = message.trim() || fallback
    if (isStatusBlockedMessage(readableMessage)) {
      toast.warning(readableMessage)
      return
    }
    toast.error(readableMessage)
  }, [])

  const loadPreview = useCallback(async () => {
    setLoading(true)
    try {
      const html = await writingService.getPreviewHtml(pid)
      setPreviewHtml(html)
    } catch (error) {
      showRequestFeedback(getReadableErrorMessage(error, '预览加载失败'), '预览加载失败')
    } finally {
      setLoading(false)
    }
  }, [pid, showRequestFeedback])

  const loadConsistencySafely = useCallback(async () => {
    try {
      await loadConsistency(pid)
    } catch (error) {
      showRequestFeedback(getReadableErrorMessage(error, '一致性检查加载失败'), '一致性检查加载失败')
    }
  }, [loadConsistency, pid, showRequestFeedback])

  useEffect(() => {
    if (!pid || !canLoadPreviewData) return
    void Promise.all([
      loadConsistencySafely(),
      loadPreview(),
    ])
  }, [pid, canLoadPreviewData, loadConsistencySafely, loadPreview])

  const handleRender = useCallback(async () => {
    setRendering(true)
    setRetryState(null)
    try {
      await writingService.triggerRender(pid)
      toast.success('渲染完成')
      await Promise.all([
        loadPreview(),
        loadConsistencySafely(),
      ])
    } catch (error) {
      showRequestFeedback(getReadableErrorMessage(error, '渲染失败'), '渲染失败')
      writingService.getRetryState(pid, 'writing_render')
        .then(setRetryState)
        .catch(() => {})
    } finally {
      setRendering(false)
    }
  }, [pid, loadConsistencySafely, loadPreview, showRequestFeedback])

  const refreshRetryState = useCallback(async () => {
    setRefreshingRetry(true)
    try {
      setRetryState(await writingService.getRetryState(pid, 'writing_render'))
    } finally {
      setRefreshingRetry(false)
    }
  }, [pid])

  useEffect(() => {
    if (ready && projectStatus === 'failed') {
      refreshRetryState().catch(() => {})
    }
  }, [ready, projectStatus, refreshRetryState])

  const handleRetryRender = useCallback(async () => {
    setRendering(true)
    try {
      await writingService.retryWritingNode(pid, 'writing_render')
      setRetryState(null)
      toast.success('重新渲染完成')
      await Promise.all([
        loadPreview(),
        loadConsistencySafely(),
      ])
    } catch (error) {
      showRequestFeedback(getReadableErrorMessage(error, '重新渲染失败'), '重新渲染失败')
      await refreshRetryState().catch(() => {})
    } finally {
      setRendering(false)
    }
  }, [pid, loadConsistencySafely, loadPreview, refreshRetryState, showRequestFeedback])

  const { high: highSeverity, other: otherIssues, info: infoIssues } =
    splitIssuesBySeverity(consistencyIssues)
  const scoringSummary = useMemo(
    () => summarizeScoringResponse(consistencyIssues),
    [consistencyIssues],
  )
  const consistencyIssueGroups = useMemo(
    () => buildConsistencyIssueGroups(consistencyIssues),
    [consistencyIssues],
  )
  const issueGroupStats = useMemo(
    () => getConsistencyIssueGroupStats(consistencyIssueGroups),
    [consistencyIssueGroups],
  )

  const handleDownload = useCallback(async () => {
    setDownloading(true)
    try {
      const blob = await writingService.downloadDocx(pid, {
        allowBlockingIssues: shouldAllowBlockingIssuesOnConfirmedDownload(highSeverity.length),
      })
      if (!(blob instanceof Blob)) {
        throw new Error('服务端返回格式异常')
      }
      downloadBlob(blob, buildExportFilename(projectName, '标书初稿', 'docx'))
      toast.success('下载完成')
    } catch (error) {
      const message = getReadableErrorMessage(error, '下载失败')
      showRequestFeedback(message, '下载失败')
    } finally {
      setDownloading(false)
    }
  }, [highSeverity.length, pid, projectName, showRequestFeedback])

  const bannerClassName = deriveBannerClassName(highSeverity.length)
  const downloadButtonLabel = deriveDownloadButtonLabel(highSeverity.length)
  const { preview: highSeverityPreview, overflowCount: highSeverityOverflow } =
    computeDownloadSeverityPreview(highSeverity)
  const { canRender, canDownload, blockedReason } =
    derivePreviewActionAvailability(projectStatus)
  const toggleIssueGroup = useCallback((groupKey: string, collapsedByDefault: boolean) => {
    setCollapsedIssueGroups((prev) => ({
      ...prev,
      [groupKey]: !(prev[groupKey] ?? collapsedByDefault),
    }))
  }, [])

  if (guardLoading || !ready) {
    return <div className="flex items-center justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-primary" /></div>
  }

  return (
    <div>
      <WritingStepBar projectId={pid} currentStep={4} projectStatus={projectStatus} />
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate(`/project/${projectId}/writing/compose`)}>
            <ArrowLeft className="h-4 w-4 mr-1" />
            返回
          </Button>
          <h1 className="text-xl font-semibold">预览与导出</h1>
        </div>
        <div className="flex gap-2">
          <ProjectTimelineButton projectId={projectId} />
          <Button
            data-testid="preview-render-button"
            variant="outline"
            onClick={handleRender}
            disabled={rendering || !canRender}
            title={!canRender ? blockedReason ?? undefined : undefined}
          >
            <RefreshCw className={`h-4 w-4 mr-1 ${rendering ? 'animate-spin' : ''}`} />
            {rendering ? '渲染中...' : '重新渲染'}
          </Button>
          <Button
            data-testid="preview-download-button"
            onClick={() => setShowDownloadConfirm(true)}
            disabled={downloading || !canDownload}
            title={!canDownload ? blockedReason ?? undefined : undefined}
          >
            {downloading
              ? <Loader2 className="h-4 w-4 mr-1 animate-spin" />
              : <Download className="h-4 w-4 mr-1" />}
            {downloading ? '下载中...' : '下载 Word'}
          </Button>
        </div>
      </div>

      <div className="max-w-5xl mx-auto">
      {retryState?.failedNode === 'writing_render' && (
        <RetryActionPanel
          state={retryState}
          onRetry={handleRetryRender}
          onRefresh={refreshRetryState}
          retrying={rendering}
          refreshing={refreshingRetry}
        />
      )}
      {consistencyIssues.length > 0 && (
        <div
          data-testid="consistency-banner"
          data-severity={highSeverity.length > 0 ? 'high' : 'normal'}
          className={`border rounded-lg p-4 mb-4 ${bannerClassName}`}
        >
          <button
            onClick={() => setShowIssues(!showIssues)}
            aria-expanded={showIssues}
            className="flex items-center gap-2 w-full text-left"
          >
            <ChevronDown
              className={`h-4 w-4 transition-transform ${
                highSeverity.length > 0 ? 'text-rose-600' : 'text-amber-600'
              } ${showIssues ? '' : '-rotate-90'}`}
            />
            <AlertTriangle
              className={`h-5 w-5 ${
                highSeverity.length > 0 ? 'text-rose-600' : 'text-amber-600'
              }`}
            />
            <span
              className={`font-medium ${
                highSeverity.length > 0 ? 'text-rose-800' : 'text-amber-800'
              }`}
            >
              检查结果: {highSeverity.length} 个严重问题, {otherIssues.length} 个一般提醒
              {infoIssues.length > 0 ? `, ${infoIssues.length} 项非正文响应说明` : ''}，
              涉及 {issueGroupStats.groupCount} 类问题
              {issueGroupStats.highGroupCount > 0 ? `（${issueGroupStats.highGroupCount} 类严重）` : ''}
              {scoringSummary.totalIssueCount > 0
                ? `；评分项检查 ${scoringSummary.totalIssueCount} 项需处理`
                : ''}
              {scoringSummary.unrespondedCount > 0
                ? `（未响应 ${scoringSummary.unrespondedCount} 项共 ${scoringSummary.unrespondedScore} 分）`
                : ''}
            </span>
          </button>
          {showIssues && (
            <div className="mt-3 space-y-2 ml-6">
              {consistencyIssueGroups.map((group) => {
                const collapsed = collapsedIssueGroups[group.key] ?? group.collapsedByDefault
                return (
                  <div
                    key={group.key}
                    data-testid={`consistency-issue-group-${group.key}`}
                    className={`rounded-md border bg-white/65 ${getIssueGroupBorderClassName(group.severity)}`}
                  >
                    <button
                      type="button"
                      onClick={() => toggleIssueGroup(group.key, group.collapsedByDefault)}
                      aria-expanded={!collapsed}
                      className="flex w-full items-start gap-2 p-3 text-left"
                    >
                      <ChevronDown
                        className={`mt-0.5 h-4 w-4 shrink-0 transition-transform ${
                          collapsed ? '-rotate-90' : ''
                        } ${getIssueGroupIconClassName(group.severity)}`}
                      />
                      <span className="min-w-0 flex-1">
                        <span className={`block text-sm font-medium ${getIssueGroupTitleClassName(group.severity)}`}>
                          {group.title}
                        </span>
                        <span className="mt-0.5 block text-xs text-muted-foreground">
                          {group.description}
                        </span>
                      </span>
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
                        getIssueGroupBadgeClassName(group.severity)
                      }`}>
                        {group.issues.length} 项 · {getIssueSeverityLabel(group.severity)}
                      </span>
                    </button>
                    {!collapsed && (
                      <ul className="space-y-1 border-t border-black/5 px-4 py-3 text-sm">
                        {group.issues.map((issue) => (
                          <li
                            key={getConsistencyIssueKey(issue)}
                            className={`${getIssueTextClassName(issue.severity)} flex gap-2`}
                          >
                            <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-current" />
                            <span>
                              <span>{formatConsistencyIssueMessage(issue)}</span>
                              {formatConsistencyIssueDetail(issue) ? (
                                <span className="mt-0.5 block text-xs opacity-80">{formatConsistencyIssueDetail(issue)}</span>
                              ) : null}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      <div className="bg-white rounded-xl border shadow-xs">
        {loading ? (
          <div className="text-center py-20 text-muted-foreground">
            <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent mx-auto mb-4" />
            加载预览...
          </div>
        ) : (
          <iframe
            srcDoc={previewHtml}
            sandbox=""
            className="w-full border-0 rounded-xl"
            style={{ minHeight: '80vh' }}
            title="标书预览"
          />
        )}
      </div>
      </div>

      <Dialog open={showDownloadConfirm} onOpenChange={setShowDownloadConfirm}>
        <DialogContent className="max-h-[calc(100vh-2rem)] flex flex-col overflow-hidden gap-4">
          <DialogHeader className="shrink-0">
            <DialogTitle>下载标书文档</DialogTitle>
            <DialogDescription>
              系统将生成 Word 格式的标书文档并下载到本地。
              文档包含各章节正文及素材引用内容。
            </DialogDescription>
          </DialogHeader>
          <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain space-y-4 pr-1">
            <div
              data-testid="download-open-tips"
              className="rounded-md border bg-muted/50 p-3 space-y-1.5 text-sm text-foreground/80"
            >
              <p className="font-medium text-foreground">下载后操作提示</p>
              <p>&middot; {DOCX_TOC_GUIDE_TEXT}</p>
              <p>&middot; {DOCX_UNLOCK_GUIDE_TEXT}</p>
            </div>
            {highSeverity.length > 0 && (
              <div
                data-testid="download-severity-warning"
                className="bg-rose-50 border border-rose-200 rounded p-3 text-sm"
              >
                <p className="font-medium text-rose-800 mb-2">
                  本次检测到 {highSeverity.length} 项严重问题
                </p>
                <ul className="max-h-[28vh] overflow-y-auto overscroll-contain text-rose-700 space-y-0.5 ml-4 list-disc pr-1">
                  {highSeverityPreview.map((issue, idx) => (
                    <li key={`dlg-h-${idx}`}>{formatConsistencyIssueMessage(issue)}</li>
                  ))}
                </ul>
                {highSeverityOverflow > 0 && (
                  <p className="text-xs text-rose-600 mt-1">
                    还有 {highSeverityOverflow} 项，详见预览页顶部检查结果
                  </p>
                )}
              </div>
            )}
            <p className="text-sm text-destructive font-medium">
              内容为 AI 生成，实际业务请以人工审核为准。
            </p>
          </div>
          <DialogFooter className="shrink-0">
            <Button variant="outline" onClick={() => setShowDownloadConfirm(false)}>取消</Button>
            <Button
              data-testid="download-confirm-button"
              onClick={() => { setShowDownloadConfirm(false); handleDownload() }}
            >
              {downloadButtonLabel}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <FirstVisitPreviewDialog />
    </div>
  )
}

function FirstVisitPreviewDialog() {
  const { open, dismiss } = useFirstVisitDialog('bidmind_writing_guide_preview')
  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) dismiss() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>预览与导出</DialogTitle>
          <DialogDescription className="text-sm text-foreground leading-relaxed">
            系统已完成一致性检查并渲染最终文档。请根据检查结果确认是否存在需要修正的问题。
          </DialogDescription>
        </DialogHeader>
        <div className="mt-3 p-3 bg-muted rounded-lg space-y-1.5 text-sm text-foreground/80">
          <p className="text-sm font-medium text-foreground mb-1">操作提示</p>
          <p>&middot; 页面上方展示检查结果，请优先关注严重问题</p>
          <p>&middot; 确认无误后可下载 Word 文档</p>
          <p>&middot; {DOCX_TOC_GUIDE_TEXT}</p>
          <p>&middot; {DOCX_UNLOCK_GUIDE_TEXT}</p>
          <p>&middot; 下载后可根据实际需要进行最终的手动完善</p>
        </div>
        <DialogFooter>
          <Button onClick={dismiss}>知道了</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function getIssueGroupBorderClassName(severity: string | null | undefined): string {
  return severity === 'high' ? 'border-rose-200' : 'border-amber-200'
}

function getIssueGroupIconClassName(severity: string | null | undefined): string {
  return severity === 'high' ? 'text-rose-600' : 'text-amber-600'
}

function getIssueGroupTitleClassName(severity: string | null | undefined): string {
  return severity === 'high' ? 'text-rose-800' : 'text-amber-800'
}

function getIssueGroupBadgeClassName(severity: string | null | undefined): string {
  if (severity === 'high') return 'bg-rose-100 text-rose-800'
  if (severity === 'medium') return 'bg-orange-100 text-orange-800'
  if (severity === 'warning') return 'bg-amber-100 text-amber-800'
  return 'bg-amber-100 text-amber-800'
}

function getIssueTextClassName(severity: string | null | undefined): string {
  return severity === 'high' ? 'text-rose-700' : 'text-amber-700'
}

function getIssueSeverityLabel(severity: string | null | undefined): string {
  if (severity === 'high') return '严重'
  if (severity === 'medium') return '中等'
  if (severity === 'warning') return '提醒'
  if (severity === 'low') return '低风险'
  return '提醒'
}

function getConsistencyIssueKey(issue: { type?: string | null; nodeId?: number | null; message?: string | null; detail?: string | null }): string {
  return [
    issue.type || 'issue',
    issue.nodeId ?? 'global',
    issue.message || '',
    issue.detail || '',
  ].join(':')
}

export default WritingPreviewPage
