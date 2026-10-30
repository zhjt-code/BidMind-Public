import React, { useEffect, useRef, useState } from 'react'
import { Thumbnail } from 'react-pdf'
import { ChevronLeft, ChevronRight, Download, ZoomIn, ZoomOut } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import {
  MAX_ZOOM,
  MIN_ZOOM,
  ZOOM_STEP,
  clampPage,
  clampZoom,
  computeRailScrollTop,
  pageNumbers,
} from './pdfPreviewMath'

/**
 * 职责: pdf.js 预览的顶栏操作与缩略图侧栏
 * 不做: 不拉 PDF、不走原生插件查看器
 */

interface PdfPreviewToolbarProps {
  pageNumber: number
  numPages: number
  zoom: number
  onPageChange: (page: number) => void
  onZoomChange: (zoom: number) => void
  onDownload: () => void
  downloadDisabled: boolean
}

export const PdfPreviewToolbar: React.FC<PdfPreviewToolbarProps> = ({
  pageNumber,
  numPages,
  zoom,
  onPageChange,
  onZoomChange,
  onDownload,
  downloadDisabled,
}) => {
  const [pageDraft, setPageDraft] = useState(String(pageNumber))

  useEffect(() => {
    setPageDraft(String(pageNumber))
  }, [pageNumber])

  const commitPageDraft = () => {
    const parsed = Number.parseInt(pageDraft, 10)
    if (Number.isNaN(parsed)) {
      setPageDraft(String(pageNumber))
      return
    }
    onPageChange(clampPage(parsed, numPages))
  }

  return (
    <div className="shrink-0 px-3 py-2 border-b flex flex-wrap items-center gap-2 bg-neutral-50">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={pageNumber <= 1}
        onClick={() => onPageChange(clampPage(pageNumber - 1, numPages))}
      >
        <ChevronLeft className="h-4 w-4" />
        上一页
      </Button>
      <label className="flex items-center gap-1 text-sm text-gray-600">
        <span className="sr-only">页码</span>
        <Input
          type="number"
          min={1}
          max={numPages || undefined}
          value={pageDraft}
          onChange={(event) => setPageDraft(event.target.value)}
          onBlur={commitPageDraft}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.currentTarget.blur()
            }
          }}
          className="h-8 w-16 px-2 text-center"
        />
        <span>/ {numPages || '—'}</span>
      </label>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={numPages > 0 && pageNumber >= numPages}
        onClick={() => onPageChange(clampPage(pageNumber + 1, numPages))}
      >
        下一页
        <ChevronRight className="h-4 w-4" />
      </Button>

      <div className="mx-1 h-5 w-px bg-gray-200" />

      <Button
        type="button"
        variant="outline"
        size="sm"
        aria-label="缩小"
        disabled={zoom <= MIN_ZOOM}
        onClick={() => onZoomChange(clampZoom(zoom - ZOOM_STEP))}
      >
        <ZoomOut className="h-4 w-4" />
      </Button>
      <span className="w-12 text-center text-sm text-gray-600">{Math.round(zoom * 100)}%</span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        aria-label="放大"
        disabled={zoom >= MAX_ZOOM}
        onClick={() => onZoomChange(clampZoom(zoom + ZOOM_STEP))}
      >
        <ZoomIn className="h-4 w-4" />
      </Button>

      <div className="ml-auto">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={downloadDisabled}
          onClick={onDownload}
        >
          <Download className="h-4 w-4" />
          下载
        </Button>
      </div>
    </div>
  )
}

interface PdfPreviewThumbRailProps {
  numPages: number
  pageNumber: number
  onPageChange: (page: number) => void
}

export const PdfPreviewThumbRail: React.FC<PdfPreviewThumbRailProps> = ({
  numPages,
  pageNumber,
  onPageChange,
}) => {
  const railRef = useRef<HTMLElement | null>(null)
  const [visiblePages, setVisiblePages] = useState<Set<number>>(() => new Set([pageNumber]))

  useEffect(() => {
    setVisiblePages((current) => {
      if (current.has(pageNumber)) return current
      const next = new Set(current)
      next.add(pageNumber)
      return next
    })
  }, [pageNumber])

  useEffect(() => {
    const rail = railRef.current
    if (!rail) return
    const slots = Array.from(rail.querySelectorAll<HTMLElement>('[data-preview-page]'))
    const observer = new IntersectionObserver(
      (entries) => {
        setVisiblePages((current) => {
          const next = new Set(current)
          for (const entry of entries) {
            const raw = entry.target.getAttribute('data-preview-page')
            const page = raw ? Number(raw) : NaN
            if (!Number.isFinite(page)) continue
            if (entry.isIntersecting) next.add(page)
          }
          return next
        })
      },
      { root: rail, rootMargin: '120px 0px' },
    )
    for (const slot of slots) observer.observe(slot)
    return () => observer.disconnect()
  }, [numPages])

  useEffect(() => {
    const rail = railRef.current
    if (!rail || numPages <= 0) return
    const apply = () => {
      const current = rail.querySelector<HTMLElement>(`[data-preview-page="${pageNumber}"]`)
      if (!current) return
      const railRect = rail.getBoundingClientRect()
      const slotRect = current.getBoundingClientRect()
      rail.scrollTop = computeRailScrollTop({
        slotTop: slotRect.top,
        slotHeight: slotRect.height,
        railTop: railRect.top,
        railClientHeight: rail.clientHeight,
        railScrollTop: rail.scrollTop,
        railScrollHeight: rail.scrollHeight,
      })
    }
    apply()
    const raf = window.requestAnimationFrame(apply)
    return () => window.cancelAnimationFrame(raf)
  }, [pageNumber, numPages])

  if (numPages <= 0) return null

  return (
    <aside
      ref={railRef}
      aria-label="页面缩略图"
      className="w-36 shrink-0 overflow-y-auto overscroll-contain border-r bg-neutral-100 p-2 space-y-2"
    >
      {pageNumbers(numPages).map((page) => {
        const selected = page === pageNumber
        return (
          <button
            key={page}
            type="button"
            data-preview-page={page}
            aria-current={selected ? 'page' : undefined}
            aria-label={`第 ${page} 页`}
            className={cn(
              'block w-full rounded border bg-white p-1 text-center',
              selected ? 'border-blue-500 ring-2 ring-blue-200' : 'border-gray-200',
            )}
            onClick={() => onPageChange(page)}
          >
            {visiblePages.has(page) ? (
              <Thumbnail
                pageNumber={page}
                width={104}
                renderMode="canvas"
                canvasBackground="white"
              />
            ) : (
              <div className="h-36 w-full bg-white" />
            )}
            <span className="mt-1 block text-xs text-gray-600">{page}</span>
          </button>
        )
      })}
    </aside>
  )
}
