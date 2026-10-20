import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { errorLogService } from '@/services/errorLogApi'
import { toast } from 'sonner'
import { Loader2 } from 'lucide-react'
import { useErrorLogFilters } from './hooks/useErrorLogFilters'
import { useErrorLogPagination } from './hooks/useErrorLogPagination'
import { useErrorLogSelection } from './hooks/useErrorLogSelection'
import { useErrorLogStats } from './hooks/useErrorLogStats'
import { useErrorLogList } from './hooks/useErrorLogList'
import { useErrorLogGroups } from './hooks/useErrorLogGroups'
import { useErrorLogDetail } from './hooks/useErrorLogDetail'
import { useErrorChain } from './hooks/useErrorChain'
import { useSuppressionRules } from './hooks/useSuppressionRules'
import { ErrorLogStats } from './components/ErrorLogStats'
import { ErrorLogRow } from './components/ErrorLogRow'
import { ErrorLogGroupCard } from './components/ErrorLogGroupCard'
import { ErrorChainDialog } from './components/ErrorChainDialog'
import { SuppressLogDialog } from './components/SuppressLogDialog'
import { SuppressionRulesDialog } from './components/SuppressionRulesDialog'
import { ErrorLogHeaderToolbar } from './components/ErrorLogHeaderToolbar'
import { ErrorLogFilterBar } from './components/ErrorLogFilterBar'
import { ErrorLogBatchBar } from './components/ErrorLogBatchBar'
import { ErrorLogPagination } from './components/ErrorLogPagination'

const ErrorLogPage: React.FC = () => {
  const navigate = useNavigate()
  const handleOpenProjectTimeline = useCallback((projectId: number) => {
    navigate(`/admin/projects/${projectId}/timeline`)
  }, [navigate])

  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(0)
  const [groupTotalPages, setGroupTotalPages] = useState(0)
  const [batchDeleting, setBatchDeleting] = useState(false)
  const [batchResolving, setBatchResolving] = useState(false)

  const [viewMode, setViewMode] = useState<'grouped' | 'flat'>('grouped')
  const [groupSortBy, setGroupSortBy] = useState<'latest_at' | 'count'>('latest_at')

  const {
    categoryFilter, taskFilter, levelFilter, sourceFilter, resolvedFilter,
    timeRange, searchInput,
    setCategoryFilter, setTaskFilter, setLevelFilter, setSourceFilter, setResolvedFilter,
    setTimeRange, setSearchInput,
    activeFilters, currentTaskOptions, hasFilters,
    handleSearch, resetFilters,
  } = useErrorLogFilters()

  const {
    page, groupPage, jumpPageInput, setJumpPageInput,
    effectiveTotalPages, handlePageChange, handleJumpToPage,
  } = useErrorLogPagination({
    viewMode, activeFilters, groupSortBy, totalPages, groupTotalPages,
  })

  const { stats, statsLoading, reload: reloadStats } = useErrorLogStats(timeRange)

  const { logs, loading, reload: reloadList } = useErrorLogList({
    viewMode, activeFilters, page,
    onTotal: setTotal, onTotalPages: setTotalPages,
  })

  const {
    groups, groupTotal, groupLoading,
    expandedFingerprint, innerLogs, innerTotal, innerPage, innerTotalPages, innerLoading,
    reloadGroups, reloadInner, handleToggleGroup: rawHandleToggleGroup, handleInnerPageChange,
    resetExpanded: resetGroupsExpanded,
  } = useErrorLogGroups({
    viewMode, activeFilters, groupPage, groupSortBy,
    onTotal: setTotal, onGroupTotalPages: setGroupTotalPages,
  })

  const {
    expandedId, detailCache, detailLoadingId, handleToggleExpand,
    reset: resetDetail,
  } = useErrorLogDetail({ page })

  // 保留原行为: 切换分组展开时, 清空当前单条日志详情展开态 (跨 hook 协调)
  const handleToggleGroup = useCallback((fingerprint: string) => {
    if (expandedFingerprint !== fingerprint) resetDetail()
    rawHandleToggleGroup(fingerprint)
  }, [expandedFingerprint, resetDetail, rawHandleToggleGroup])

  const {
    selectionMode, setSelectionMode,
    allMatchingSelected, excludedIds, selectedFingerprints,
    isLogSelected, selectedCount, currentPageSelectedCount, allCurrentPageSelected,
    clearSelection, exitSelectionMode, selectAllMatchingLogs,
    toggleSelect, toggleSelectAll, toggleGroupSelect,
    getBatchPayload,
  } = useErrorLogSelection({
    viewMode, activeFilters, logs, groups, total, page,
  })

  const {
    chainIncidentId, chainItems, chainLoading,
    openChain: handleOpenChain, closeChain,
  } = useErrorChain()

  const reloadCurrentView = useCallback(() => {
    if (viewMode === 'grouped') reloadGroups()
    else reloadList()
    reloadStats()
  }, [viewMode, reloadGroups, reloadList, reloadStats])

  const {
    suppressTarget, setSuppressTarget,
    suppressReason, setSuppressReason,
    suppressSubmitting, handleSuppress,
    rulesDialogOpen, setRulesDialogOpen,
    rules, rulesLoading, rulesTotal, rulesPage, rulesTotalPages,
    loadRules, handleOpenRulesDialog,
    handleToggleRule, handleDeleteRule,
    recomputingFp, handleRecomputeFingerprints,
  } = useSuppressionRules({ onReload: reloadCurrentView })

  // viewMode 切换时同步清空 detail 和 groups 展开态（原行为：跨状态级联）
  useEffect(() => {
    resetDetail()
    resetGroupsExpanded()
  }, [viewMode, resetDetail, resetGroupsExpanded])

  const handleResolve = useCallback(async (id: number) => {
    try {
      await errorLogService.resolve(id)
      toast.success('已标记为已处理')
      reloadCurrentView()
      if (expandedFingerprint) reloadInner()
    } catch {
      toast.error('操作失败')
    }
  }, [reloadCurrentView, expandedFingerprint, reloadInner])

  const handleBatchDelete = useCallback(async () => {
    if (selectedCount === 0) return
    if (!window.confirm(`确认删除选中的 ${selectedCount} 条日志？此操作不可恢复。`)) return

    setBatchDeleting(true)
    try {
      const res = await errorLogService.batchDelete(getBatchPayload()) as {
        data?: { deleted?: number }
      }
      const deleted = res?.data?.deleted || 0
      toast.success(`已删除 ${deleted} 条日志`)
      reloadCurrentView()
      exitSelectionMode()
    } catch {
      toast.error('批量删除失败')
    } finally {
      setBatchDeleting(false)
    }
  }, [selectedCount, getBatchPayload, reloadCurrentView, exitSelectionMode])

  const handleBatchResolve = useCallback(async () => {
    if (selectedCount === 0) return

    setBatchResolving(true)
    try {
      const res = await errorLogService.batchResolve(getBatchPayload()) as {
        data?: { resolved?: number; already_resolved?: number }
      }
      const resolved = res?.data?.resolved || 0
      const already = res?.data?.already_resolved || 0
      const msg = already > 0 ? `已标记 ${resolved} 条，${already} 条已处理` : `已标记 ${resolved} 条为已处理`
      toast.success(msg)
      reloadCurrentView()
      exitSelectionMode()
    } catch {
      toast.error('批量标记失败')
    } finally {
      setBatchResolving(false)
    }
  }, [selectedCount, getBatchPayload, reloadCurrentView, exitSelectionMode])

  return (
    <div>
      <ErrorLogHeaderToolbar
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        groupSortBy={groupSortBy}
        onGroupSortByChange={setGroupSortBy}
        total={total}
        groupTotal={groupTotal}
        recomputingFp={recomputingFp}
        onRecomputeFingerprints={handleRecomputeFingerprints}
        onOpenRulesDialog={handleOpenRulesDialog}
        selectionMode={selectionMode}
        onEnterSelectionMode={() => setSelectionMode(true)}
        onExitSelectionMode={exitSelectionMode}
      />

      <ErrorLogStats stats={stats} loading={statsLoading} />

      <ErrorLogFilterBar
        categoryFilter={categoryFilter}
        taskFilter={taskFilter}
        levelFilter={levelFilter}
        sourceFilter={sourceFilter}
        resolvedFilter={resolvedFilter}
        timeRange={timeRange}
        searchInput={searchInput}
        currentTaskOptions={currentTaskOptions}
        hasFilters={hasFilters}
        onCategoryChange={setCategoryFilter}
        onTaskChange={setTaskFilter}
        onLevelChange={setLevelFilter}
        onSourceChange={setSourceFilter}
        onResolvedChange={setResolvedFilter}
        onTimeRangeChange={setTimeRange}
        onSearchInputChange={setSearchInput}
        onSearch={handleSearch}
        onReset={resetFilters}
      />

      {selectionMode && (
        <ErrorLogBatchBar
          viewMode={viewMode}
          logsLength={logs.length}
          groupsLength={groups.length}
          total={total}
          allCurrentPageSelected={allCurrentPageSelected}
          currentPageSelectedCount={currentPageSelectedCount}
          allMatchingSelected={allMatchingSelected}
          excludedIdsSize={excludedIds.size}
          selectedCount={selectedCount}
          selectedFingerprintsSize={selectedFingerprints.size}
          batchDeleting={batchDeleting}
          batchResolving={batchResolving}
          onToggleSelectAll={toggleSelectAll}
          onSelectAllMatching={selectAllMatchingLogs}
          onClearSelection={clearSelection}
          onBatchResolve={handleBatchResolve}
          onBatchDelete={handleBatchDelete}
        />
      )}

      {viewMode === 'grouped' && (
        <>
          {groupLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
            </div>
          ) : groups.length === 0 ? (
            <div className="text-center py-16 text-gray-400 text-sm">
              {hasFilters ? '没有匹配的错误日志' : '暂无错误日志'}
            </div>
          ) : (
            <div>
              {groups.map(group => (
                <ErrorLogGroupCard
                  key={group.fingerprint}
                  group={group}
                  expanded={expandedFingerprint === group.fingerprint}
                  onToggle={() => handleToggleGroup(group.fingerprint)}
                  innerLogs={expandedFingerprint === group.fingerprint ? innerLogs : []}
                  innerTotal={expandedFingerprint === group.fingerprint ? innerTotal : 0}
                  innerPage={expandedFingerprint === group.fingerprint ? innerPage : 1}
                  innerTotalPages={expandedFingerprint === group.fingerprint ? innerTotalPages : 0}
                  innerLoading={expandedFingerprint === group.fingerprint && innerLoading}
                  onInnerPageChange={handleInnerPageChange}
                  detailCache={detailCache}
                  detailLoadingId={detailLoadingId}
                  expandedLogId={expandedId}
                  onLogToggle={handleToggleExpand}
                  onResolve={handleResolve}
                  onSuppress={setSuppressTarget}
                  selectionMode={selectionMode}
                  groupSelected={selectedFingerprints.has(group.fingerprint)}
                  onToggleGroupSelect={toggleGroupSelect}
                  onOpenChain={handleOpenChain}
                  onOpenProjectTimeline={handleOpenProjectTimeline}
                />
              ))}
            </div>
          )}
        </>
      )}

      {viewMode === 'flat' && (
        <>
          {loading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
            </div>
          ) : logs.length === 0 ? (
            <div className="text-center py-16 text-gray-400 text-sm">
              {hasFilters ? '没有匹配的错误日志' : '暂无错误日志'}
            </div>
          ) : (
            <div>
              {logs.map(log => (
                <ErrorLogRow
                  key={log.id}
                  log={log}
                  detail={detailCache[log.id] || null}
                  detailLoading={detailLoadingId === log.id}
                  expanded={expandedId === log.id}
                  onToggle={() => handleToggleExpand(log.id)}
                  onResolve={handleResolve}
                  onSuppress={setSuppressTarget}
                  selectionMode={selectionMode}
                  selected={isLogSelected(log.id)}
                  onToggleSelect={toggleSelect}
                  onOpenChain={handleOpenChain}
                  onOpenProjectTimeline={handleOpenProjectTimeline}
                />
              ))}
            </div>
          )}
        </>
      )}

      <ErrorLogPagination
        currentPage={viewMode === 'grouped' ? groupPage : page}
        totalPages={effectiveTotalPages}
        jumpPageInput={jumpPageInput}
        onJumpPageInputChange={setJumpPageInput}
        onPageChange={handlePageChange}
        onJumpToPage={handleJumpToPage}
      />

      <ErrorChainDialog
        incidentId={chainIncidentId}
        items={chainItems}
        loading={chainLoading}
        onClose={closeChain}
      />

      <SuppressLogDialog
        target={suppressTarget}
        reason={suppressReason}
        submitting={suppressSubmitting}
        onReasonChange={setSuppressReason}
        onCancel={() => { setSuppressTarget(null); setSuppressReason('') }}
        onConfirm={handleSuppress}
      />

      <SuppressionRulesDialog
        open={rulesDialogOpen}
        onOpenChange={setRulesDialogOpen}
        rules={rules}
        loading={rulesLoading}
        total={rulesTotal}
        page={rulesPage}
        totalPages={rulesTotalPages}
        onPageChange={loadRules}
        onToggleRule={handleToggleRule}
        onDeleteRule={handleDeleteRule}
      />
    </div>
  )
}

export default ErrorLogPage
