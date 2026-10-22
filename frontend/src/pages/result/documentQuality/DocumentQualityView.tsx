import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { EmptyState, SkeletonCard } from '@/components'
import { documentQualityService } from '@/services/documentQualityApi'
import { ResizableLeftPane } from '../../ResizableLeftPane'
import { ResizableMiddlePane } from '../../ResizableMiddlePane'
import {
  RESULT_LEFT_PANE_DEFAULT_PERCENT,
  RESULT_MIDDLE_PANE_DEFAULT_PERCENT,
} from '../../resultMiddlePaneWidth'
import {
  RESULT_PAGE_CONTENT_SHELL_CLASS,
  RESULT_PAGE_DETAIL_PANE_CLASS,
} from '../../resultPageLayout'
import { ScrollArea } from '@/components/ui/scroll-area'
import DocumentQualityDetail from './DocumentQualityDetail'
import DocumentQualityList from './DocumentQualityList'
import DocumentQualityNav from './DocumentQualityNav'
import DocumentQualityStats from './DocumentQualityStats'
import {
  countFindings,
  filterFindings,
  sortFindings,
} from './documentQualityTransforms'
import { DEFAULT_QA_FILTERS, type DocumentQualityData, type QaFilterState, type QaFinding } from './types'

/**
 * 职责: 文档质检视图容器(结果页"文档质检"切换项)——筛选/列表/详情/处置编排
 * 不做: 不承载响应审查视图、不做检查计算
 */

interface DocumentQualityViewProps {
  projectId: string
  data: DocumentQualityData | null
  loading: boolean
  error: string | null
  onReload: () => void
}

const DocumentQualityView: React.FC<DocumentQualityViewProps> = ({
  projectId, data, loading, error, onReload,
}) => {
  const [filters, setFilters] = useState<QaFilterState>(DEFAULT_QA_FILTERS)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [disposing, setDisposing] = useState(false)

  const sorted = useMemo(() => sortFindings(data?.findings ?? []), [data])
  const filtered = useMemo(() => filterFindings(sorted, filters), [sorted, filters])
  const counts = useMemo(() => countFindings(sorted, filters), [sorted, filters])

  // 选中项被筛掉/首次加载时自动选中第一条
  useEffect(() => {
    if (filtered.length === 0) {
      setSelectedId(null)
      return
    }
    if (selectedId === null || !filtered.some(f => f.id === selectedId)) {
      setSelectedId(filtered[0].id)
    }
  }, [filtered, selectedId])

  const selected = useMemo(
    () => (data?.findings ?? []).find(f => f.id === selectedId) ?? null,
    [data, selectedId],
  )

  const handleFilterChange = useCallback((partial: Partial<QaFilterState>) => {
    setFilters(prev => ({ ...prev, ...partial }))
  }, [])

  const handleDispose = useCallback(async (
    finding: QaFinding, disposition: string, note: string,
  ) => {
    setDisposing(true)
    try {
      await documentQualityService.updateDisposition(parseInt(projectId), finding.id, {
        disposition,
        note: note || null,
        expectedVersion: finding.version,
      })
      toast.success('处置已保存')
      onReload()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '处置保存失败')
      onReload() // 版本冲突等场景刷新到最新状态
    } finally {
      setDisposing(false)
    }
  }, [projectId, onReload])

  if (loading && !data) {
    return (
      <div className="flex-1 px-1 space-y-3">
        <SkeletonCard lines={2} hasTitle />
        <SkeletonCard lines={4} hasTitle />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex-1 bg-white rounded-lg shadow mx-1">
        <EmptyState
          variant="chart"
          title="文档质检结果加载失败"
          description={error}
          action={{ label: '重新加载', onClick: onReload }}
        />
      </div>
    )
  }

  if (!data || (data.scan.status === 'missing' && data.findings.length === 0)) {
    return (
      <div className="flex-1 bg-white rounded-lg shadow mx-1">
        <EmptyState
          variant="chart"
          title="暂无文档质检结果"
          description="投标文件处理完成后自动生成；历史项目将在下次启动审查时自动补算"
          action={{ label: '刷新', onClick: onReload }}
        />
      </div>
    )
  }

  if (data.scan.status === 'building' && data.findings.length === 0) {
    return (
      <div className="flex-1 bg-white rounded-lg shadow mx-1">
        <EmptyState
          variant="chart"
          title="文档质检进行中"
          description="部分投标分卷尚未完成质检，齐套后自动汇总"
          action={{ label: '刷新', onClick: onReload }}
        />
      </div>
    )
  }

  return (
    <>
      <div className="flex-shrink-0 px-1">
        <DocumentQualityStats data={data} />
      </div>
      <div className={RESULT_PAGE_CONTENT_SHELL_CLASS}>
        <ResizableLeftPane defaultWidthPercent={RESULT_LEFT_PANE_DEFAULT_PERCENT}>
          <DocumentQualityNav
            filters={filters}
            counts={counts}
            onFilterChange={handleFilterChange}
            shownCount={filtered.length}
            totalCount={sorted.length}
          />
        </ResizableLeftPane>
        <ResizableMiddlePane defaultWidthPercent={RESULT_MIDDLE_PANE_DEFAULT_PERCENT}>
          <ScrollArea className="flex-1 min-h-0">
            <DocumentQualityList
              findings={filtered}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
          </ScrollArea>
        </ResizableMiddlePane>
        <div className={RESULT_PAGE_DETAIL_PANE_CLASS}>
          <DocumentQualityDetail
            finding={selected}
            projectId={projectId}
            disposing={disposing}
            onDispose={handleDispose}
          />
        </div>
      </div>
    </>
  )
}

export default DocumentQualityView
