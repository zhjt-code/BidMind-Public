import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import { Loader2, AlertTriangle } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { PdfPreviewThumbRail, PdfPreviewToolbar } from './pdfPreview/PdfPreviewChrome'
import PdfBBoxOverlay, { type PdfHighlight } from './pdfPreview/PdfBBoxOverlay'
import { locateHighlights } from './pdfPreview/locateHighlights'
import { PDFJS_DOCUMENT_OPTIONS, PDFJS_WORKER_SRC } from './pdfPreview/pdfjsRuntime'
import { selectPdfjsTextItems, textItemsToRegions } from './pdfPreview/pdfjsTextRegions'
import {
  clampPage,
  clonePdfBytes,
  copyPdfBytes,
  downloadPdfBytes,
  formatPdfParseError,
  resolveWheelPageTurn,
} from './pdfPreview/pdfPreviewMath'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'

/**
 * 职责: 鉴权拉取 PDF，用 pdf.js 画布预览指定页并提供查看器 chrome
 * 不做: 不把栅格化交给浏览器原生 PDF 插件（iframe/embed/object）
 */
pdfjs.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_SRC

interface PdfPreviewDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  pdfUrl: string
  initialPage: number
  accessToken: string | null
  /** 可选证据高亮(归一化 bbox); 仅当前页的矩形会被绘制 */
  highlights?: PdfHighlight[]
  /** 可选原文摘录; 当前页文本区按 locateHighlights 规则补框, 失败不加整页框 */
  excerpts?: string[]
}

interface PreviewPdfPage {
  getViewport: (params: { scale: number }) => { width: number; height: number; transform: number[] }
  getTextContent: () => Promise<{ items: unknown[] }>
}

interface PreviewPdfDocument {
  numPages: number
  getPage: (pageNumber: number) => Promise<PreviewPdfPage>
}

const PdfPreviewDialog: React.FC<PdfPreviewDialogProps> = ({
  open, onOpenChange, pdfUrl, initialPage, accessToken, highlights, excerpts,
}) => {
  const [pdfData, setPdfData] = useState<Uint8Array | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [numPages, setNumPages] = useState(0)
  const [pageNumber, setPageNumber] = useState(1)
  const [zoom, setZoom] = useState(1)
  const [pageWidth, setPageWidth] = useState(720)
  const [pdfDoc, setPdfDoc] = useState<PreviewPdfDocument | null>(null)
  const [locatedHighlights, setLocatedHighlights] = useState<PdfHighlight[]>([])
  const abortRef = useRef<AbortController | null>(null)
  const frameRef = useRef<HTMLDivElement | null>(null)
  const pendingAlignRef = useRef<'start' | 'end' | null>(null)
  const lastTurnAtRef = useRef(0)
  const excerptKey = (excerpts || []).join('\u0001')

  const pdfFile = useMemo(() => {
    if (!pdfData) return null
    return { data: clonePdfBytes(pdfData) }
  }, [pdfData])

  const fetchPdf = useCallback(async () => {
    if (!pdfUrl) return

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    setError(null)
    setPdfData(null)
    setNumPages(0)
    setPdfDoc(null)
    setLocatedHighlights([])
    setZoom(1)
    setPageNumber(Math.max(1, initialPage || 1))

    try {
      const doFetch = async (token: string | null) => {
        const headers: Record<string, string> = {}
        if (token) headers.Authorization = `Bearer ${token}`
        const res = await fetch(pdfUrl, { headers, signal: controller.signal })
        return res
      }

      let res = await doFetch(accessToken)

      if (res.status === 401) {
        const { refreshToken } = await import('@/stores').then(m => m.useAuthStore.getState())
        if (refreshToken) {
          try {
            const resp = await fetch('/api/auth/refresh', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ refresh_token: refreshToken }),
            })
            if (resp.ok) {
              const data = await resp.json()
              const newToken = data?.data?.access_token
              if (newToken) {
                const { setTokens } = await import('@/stores').then(m => m.useAuthStore.getState())
                setTokens({ accessToken: newToken, refreshToken: data.data.refresh_token || refreshToken, tokenType: 'Bearer' })
                res = await doFetch(newToken)
              }
            }
          } catch { /* refresh failed, fall through */ }
        }
      }

      if (!res.ok) {
        throw new Error(res.status === 401
          ? '登录已过期，请刷新页面后重试'
          : `HTTP ${res.status}`)
      }

      const buffer = await res.arrayBuffer()
      if (controller.signal.aborted) return

      setPdfData(copyPdfBytes(buffer))
    } catch (err) {
      if ((err as Error).name === 'AbortError') return
      setError(`PDF 加载失败: ${(err as Error).message || '未知错误'}`)
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }, [pdfUrl, accessToken, initialPage])

  useEffect(() => {
    if (open) {
      fetchPdf()
    } else {
      abortRef.current?.abort()
      setPdfData(null)
      setNumPages(0)
      setPdfDoc(null)
      setLocatedHighlights([])
    }
    return () => { abortRef.current?.abort() }
  }, [open, pdfUrl])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setPageNumber(Math.max(1, initialPage || 1))
  }, [initialPage, open])

  useEffect(() => {
    if (!open) return
    const el = frameRef.current
    if (!el) return
    const update = () => setPageWidth(Math.max(320, el.clientWidth - 24))
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [open, pdfData])

  useEffect(() => {
    const el = frameRef.current
    if (!el || !open || !pdfFile) return
    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey) return
      const result = resolveWheelPageTurn({
        deltaY: event.deltaY,
        pageNumber,
        numPages,
        scrollTop: el.scrollTop,
        scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
        nowMs: Date.now(),
        lastTurnAtMs: lastTurnAtRef.current,
      })
      if (result.kind === 'scroll') return
      event.preventDefault()
      if (result.kind === 'ignore') return
      pendingAlignRef.current = result.align
      lastTurnAtRef.current = Date.now()
      setPageNumber(result.page)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [open, pdfFile, pageNumber, numPages])

  useEffect(() => {
    const el = frameRef.current
    if (!el) return
    const align = pendingAlignRef.current ?? 'start'
    const apply = () => {
      if (align === 'end') {
        el.scrollTop = Math.max(0, el.scrollHeight - el.clientHeight)
      } else {
        el.scrollTop = 0
      }
    }
    apply()
    const raf = window.requestAnimationFrame(() => {
      apply()
      pendingAlignRef.current = null
    })
    return () => window.cancelAnimationFrame(raf)
  }, [pageNumber, pdfFile])

  const handleDocumentLoad = useCallback((loaded: PreviewPdfDocument) => {
    setPdfDoc(loaded)
    setNumPages(loaded.numPages)
    setPageNumber((current) => clampPage(current, loaded.numPages))
  }, [])

  useEffect(() => {
    if (!open || !pdfDoc || !excerptKey) {
      setLocatedHighlights([])
      return
    }
    let cancelled = false
    const run = async () => {
      try {
        const pdfPage = await pdfDoc.getPage(pageNumber)
        const viewport = pdfPage.getViewport({ scale: 1 })
        const content = await pdfPage.getTextContent()
        const items = selectPdfjsTextItems(content.items)
        const regions = textItemsToRegions(items, viewport)
        if (!cancelled) {
          setLocatedHighlights(locateHighlights(pageNumber, excerpts || [], regions))
        }
      } catch {
        if (!cancelled) setLocatedHighlights([])
      }
    }
    void run()
    return () => { cancelled = true }
  }, [open, pdfDoc, pageNumber, excerptKey, excerpts])

  if (!open) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-7xl w-[92vw] h-[90vh] flex flex-col p-0 gap-0 overflow-hidden">
        <DialogHeader className="px-4 pt-4 pb-2 flex-shrink-0">
          <DialogTitle className="text-base">
            PDF 原文预览
            <span className="text-sm text-gray-500 ml-2">
              （第 {pageNumber} 页{numPages > 0 ? ` / ${numPages}` : ''}）
            </span>
          </DialogTitle>
        </DialogHeader>

        {pdfFile && !error && (
          <PdfPreviewToolbar
            pageNumber={pageNumber}
            numPages={numPages}
            zoom={zoom}
            onPageChange={setPageNumber}
            onZoomChange={setZoom}
            downloadDisabled={!pdfData}
            onDownload={() => {
              if (pdfData) downloadPdfBytes(pdfData)
            }}
          />
        )}

        <div className="flex-1 min-h-0 relative flex bg-neutral-200">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center bg-gray-100 z-10">
              <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
            </div>
          )}
          {error && (
            <div className="absolute inset-0 flex flex-col items-center justify-center bg-gray-100 z-10 gap-2">
              <AlertTriangle className="h-8 w-8 text-amber-500" />
              <p className="text-sm text-gray-500 px-6 text-center">{error}</p>
              <Button variant="outline" size="sm" onClick={fetchPdf}>重试</Button>
            </div>
          )}
          {pdfFile && !error && (
            <Document
              file={pdfFile}
              options={PDFJS_DOCUMENT_OPTIONS}
              className="flex flex-1 min-h-0 w-full"
              loading={
                <div className="flex flex-1 items-center justify-center">
                  <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
                </div>
              }
              error={<p className="text-sm text-gray-500 py-16 px-6">PDF 解析失败</p>}
              onLoadSuccess={handleDocumentLoad}
              onLoadError={(loadError) => setError(formatPdfParseError(loadError))}
              onItemClick={({ pageNumber: nextPage }) => {
                setPageNumber(clampPage(nextPage, numPages))
              }}
            >
              <PdfPreviewThumbRail
                numPages={numPages}
                pageNumber={pageNumber}
                onPageChange={setPageNumber}
              />
              <div ref={frameRef} className="flex-1 min-h-0 overflow-auto overscroll-contain">
                <div className="flex justify-center p-3">
                  <div className="relative">
                    <Page
                      key={pageNumber}
                      pageNumber={pageNumber}
                      width={Math.max(160, Math.round(pageWidth * zoom))}
                      canvasBackground="white"
                      renderMode="canvas"
                    />
                    <PdfBBoxOverlay
                      highlights={[
                        ...(highlights || []),
                        ...locatedHighlights,
                      ].filter(h => h.page === pageNumber)}
                    />
                  </div>
                </div>
              </div>
            </Document>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

export default PdfPreviewDialog
