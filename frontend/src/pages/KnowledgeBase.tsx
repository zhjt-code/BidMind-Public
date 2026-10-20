import React, { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, BookOpen, Loader2, MoreHorizontal, Pencil, Power, RefreshCw, Trash2, Upload, X } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { knowledgeBaseService, type KnowledgeAtom, type KnowledgeDocument, type KnowledgeDocumentUpdatePayload } from '@/services/knowledgeBaseApi'
import {
  KNOWLEDGE_LIBRARY_CATEGORY_LABELS,
  KNOWLEDGE_LIBRARY_TYPE_OPTIONS,
  canAutoUseKnowledgeDocument,
  formatKnowledgeFileSize,
  getDefaultBusinessTypeForCategory,
  getDefaultFlowsForKnowledgeType,
  getKnowledgeFileKey,
  getKnowledgeBusinessTypeLabel,
  getKnowledgeLibraryCategory,
  getKnowledgeUnavailableReason,
  validateKnowledgeFile,
  type KnowledgeLibraryCategory,
} from '@/services/knowledgeBaseView'

const FLOW_OPTIONS = [
  { value: 'bid_writing', label: '标书编写' },
  { value: 'bid_review', label: '投标审查' },
  { value: 'collusion_check', label: '串标审查' },
]

const STATUS_LABELS: Record<string, string> = {
  saved: '待处理',
  processing: '处理中',
  completed: '已索引',
  failed: '失败',
}

const CATEGORY_ORDER: KnowledgeLibraryCategory[] = ['enterprise', 'standard', 'experience']

interface EditFormState {
  title: string
  businessType: string
  flows: string[]
}

function stripExtension(filename: string): string {
  return filename.replace(/\.[^.]+$/, '')
}

const KnowledgeBase: React.FC = () => {
  const [docs, setDocs] = useState<KnowledgeDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [processingId, setProcessingId] = useState<number | null>(null)
  const [mutatingId, setMutatingId] = useState<number | null>(null)
  const [flowFilter, setFlowFilter] = useState('')
  const [categoryFilter, setCategoryFilter] = useState<'all' | KnowledgeLibraryCategory>('all')
  const [libraryCategory, setLibraryCategory] = useState<KnowledgeLibraryCategory>('enterprise')
  const [businessType, setBusinessType] = useState(getDefaultBusinessTypeForCategory('enterprise'))
  const [files, setFiles] = useState<File[]>([])
  const [flows, setFlows] = useState<string[]>(getDefaultFlowsForKnowledgeType('company_profile'))
  const [uploadProgressText, setUploadProgressText] = useState('')
  const [editingDoc, setEditingDoc] = useState<KnowledgeDocument | null>(null)
  const [editForm, setEditForm] = useState<EditFormState | null>(null)
  const [savingEdit, setSavingEdit] = useState(false)
  const [expandedDocIds, setExpandedDocIds] = useState<number[]>([])
  const [atomMap, setAtomMap] = useState<Record<number, KnowledgeAtom[]>>({})
  const [atomLoadingId, setAtomLoadingId] = useState<number | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const loadDocs = async (options?: { silent?: boolean }) => {
    if (!options?.silent) {
      setLoading(true)
    }
    try {
      const items = await knowledgeBaseService.listDocuments({
        applicableFlow: flowFilter || undefined,
        includeDisabled: true,
      })
      setDocs(
        categoryFilter === 'all'
          ? items
          : items.filter((doc) => getKnowledgeLibraryCategory(doc) === categoryFilter),
      )
    } catch {
      toast.error('知识库列表加载失败')
    } finally {
      if (!options?.silent) {
        setLoading(false)
      }
    }
  }

  useEffect(() => {
    loadDocs()
  }, [flowFilter, categoryFilter])

  const hasProcessingDocs = useMemo(
    () => docs.some((doc) => doc.processingStatus === 'saved' || doc.processingStatus === 'processing'),
    [docs],
  )

  useEffect(() => {
    if (!hasProcessingDocs) return undefined
    const timer = window.setInterval(() => {
      loadDocs({ silent: true })
    }, 2000)
    return () => window.clearInterval(timer)
  }, [hasProcessingDocs, flowFilter, categoryFilter])

  const stats = useMemo(() => docs.reduce(
    (acc, doc) => {
      if (canAutoUseKnowledgeDocument(doc)) acc.usable += 1
      if (doc.processingStatus === 'failed') acc.failed += 1
      if (!doc.enabled) acc.disabled += 1
      return acc
    },
    { usable: 0, failed: 0, disabled: 0 },
  ), [docs])

  const changeLibraryCategory = (category: KnowledgeLibraryCategory) => {
    const nextType = getDefaultBusinessTypeForCategory(category)
    setLibraryCategory(category)
    setBusinessType(nextType)
    setFlows(getDefaultFlowsForKnowledgeType(nextType))
  }

  const changeBusinessType = (nextType: string) => {
    setBusinessType(nextType)
    setFlows(getDefaultFlowsForKnowledgeType(nextType))
  }

  const toggleFlow = (flow: string) => {
    setFlows((prev) => prev.includes(flow) ? prev.filter((item) => item !== flow) : [...prev, flow])
  }

  const handleFilesSelected = (selectedFiles: FileList | null) => {
    const incoming = Array.from(selectedFiles || [])
    if (incoming.length === 0) return
    const existingKeys = new Set(files.map(getKnowledgeFileKey))
    const nextFiles = [...files]
    const duplicateNames: string[] = []
    const invalidMessages: string[] = []

    for (const file of incoming) {
      const invalidMessage = validateKnowledgeFile(file)
      if (invalidMessage) {
        invalidMessages.push(invalidMessage)
        continue
      }
      const key = getKnowledgeFileKey(file)
      if (existingKeys.has(key)) {
        duplicateNames.push(file.name)
        continue
      }
      existingKeys.add(key)
      nextFiles.push(file)
    }

    if (invalidMessages.length > 0) {
      toast.warning(invalidMessages[0])
    }
    if (duplicateNames.length > 0) {
      toast.info(`已忽略重复文件: ${duplicateNames.slice(0, 3).join('、')}`)
    }
    setFiles(nextFiles)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const removeSelectedFile = (fileKey: string) => {
    setFiles((prev) => prev.filter((file) => getKnowledgeFileKey(file) !== fileKey))
  }

  const clearSelectedFiles = () => {
    setFiles([])
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const toggleEditFlow = (flow: string) => {
    setEditForm((prev) => {
      if (!prev) return prev
      return {
        ...prev,
        flows: prev.flows.includes(flow)
          ? prev.flows.filter((item) => item !== flow)
          : [...prev.flows, flow],
      }
    })
  }

  const handleUpload = async () => {
    if (files.length === 0) {
      toast.warning('请先选择资料文件')
      return
    }
    if (flows.length === 0) {
      toast.warning('至少选择一个适用流程')
      return
    }

    setUploading(true)
    setUploadProgressText('')
    try {
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index]
        setUploadProgressText(`正在上传 ${index + 1}/${files.length}: ${file.name}`)
        let doc: KnowledgeDocument
        try {
          doc = await knowledgeBaseService.uploadDocument({
            file,
            title: stripExtension(file.name),
            businessType,
            applicableFlows: flows,
            authority: 'user_experience',
            ownerScope: 'personal',
          })
        } catch (err) {
          const reason = err instanceof Error ? err.message : String(err)
          throw new Error(`${file.name} 上传失败: ${reason}`)
        }
        setUploadProgressText(`正在启动索引 ${index + 1}/${files.length}: ${file.name}`)
        try {
          await knowledgeBaseService.processDocument(doc.id)
        } catch (err) {
          const reason = err instanceof Error ? err.message : String(err)
          throw new Error(`${file.name} 索引任务启动失败: ${reason}`)
        }
      }
      toast.success(`已上传 ${files.length} 份知识资料，索引任务已启动`)
      setFiles([])
      setUploadProgressText('')
      await loadDocs()
    } catch (err) {
      const message = err instanceof Error ? err.message : '知识资料上传失败'
      setUploadProgressText(message)
      toast.error(message)
    } finally {
      setUploading(false)
    }
  }

  const handleProcess = async (doc: KnowledgeDocument) => {
    setProcessingId(doc.id)
    try {
      await knowledgeBaseService.processDocument(doc.id)
      toast.success('知识资料已重新索引')
      await loadDocs()
    } catch {
      toast.error('重新索引失败')
    } finally {
      setProcessingId(null)
    }
  }

  const handleToggleEnabled = async (doc: KnowledgeDocument) => {
    setMutatingId(doc.id)
    try {
      await knowledgeBaseService.updateDocument(doc.id, { enabled: !doc.enabled })
      toast.success(doc.enabled ? '知识资料已停用' : '知识资料已启用')
      await loadDocs()
    } catch {
      toast.error('状态更新失败')
    } finally {
      setMutatingId(null)
    }
  }

  const handleDeleteDocument = async (doc: KnowledgeDocument) => {
    if (!window.confirm(`确定删除知识资料“${doc.title}”？此操作不可撤销。`)) return
    setMutatingId(doc.id)
    try {
      await knowledgeBaseService.deleteDocument(doc.id)
      toast.success('知识资料已删除')
      await loadDocs()
    } catch {
      toast.error('删除失败')
    } finally {
      setMutatingId(null)
    }
  }

  const toggleAtoms = async (doc: KnowledgeDocument) => {
    const expanded = expandedDocIds.includes(doc.id)
    if (expanded) {
      setExpandedDocIds((prev) => prev.filter((id) => id !== doc.id))
      return
    }
    setExpandedDocIds((prev) => [...prev, doc.id])
    if (atomMap[doc.id]) return
    setAtomLoadingId(doc.id)
    try {
      const atoms = await knowledgeBaseService.listAtoms(doc.id)
      setAtomMap((prev) => ({ ...prev, [doc.id]: atoms }))
    } catch {
      toast.error('原子素材加载失败')
    } finally {
      setAtomLoadingId(null)
    }
  }

  const openEditDialog = (doc: KnowledgeDocument) => {
    setEditingDoc(doc)
    setEditForm({
      title: doc.title,
      businessType: doc.businessType,
      flows: doc.applicableFlows,
    })
  }

  const handleSaveEdit = async () => {
    if (!editingDoc || !editForm) return
    if (!editForm.title.trim()) {
      toast.warning('请填写资料标题')
      return
    }
    if (editForm.flows.length === 0) {
      toast.warning('至少选择一个适用流程')
      return
    }
    setSavingEdit(true)
    try {
      const payload: KnowledgeDocumentUpdatePayload = {
        title: editForm.title.trim(),
        businessType: editForm.businessType,
        applicableFlows: editForm.flows,
      }
      await knowledgeBaseService.updateDocument(editingDoc.id, payload)
      toast.success('知识资料已更新')
      setEditingDoc(null)
      setEditForm(null)
      await loadDocs()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '更新失败')
    } finally {
      setSavingEdit(false)
    }
  }

  const renderFlowToggles = (selectedFlows: string[], onToggle: (flow: string) => void) => (
    <div className="flex flex-wrap gap-2">
      {FLOW_OPTIONS.map((flow) => (
        <button
          key={flow.value}
          type="button"
          className={`px-2.5 py-1 rounded-full text-xs border ${selectedFlows.includes(flow.value) ? 'bg-primary text-white border-primary' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
          onClick={() => onToggle(flow.value)}
        >
          {flow.label}
        </button>
      ))}
    </div>
  )

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <BookOpen className="h-5 w-5 text-primary" />
            <h1 className="text-xl font-semibold">知识库</h1>
          </div>
          <p className="text-sm text-gray-500 mt-1">
            管理企业资料、规范标准和审查经验，供三大流程按门控规则引用。
          </p>
        </div>
        <Button variant="outline" onClick={() => loadDocs()} disabled={loading}>
          <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </Button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-1 bg-white border rounded-xl p-4 space-y-4">
          <h2 className="font-medium">上传知识资料</h2>

          <div className="grid grid-cols-3 gap-2">
            {CATEGORY_ORDER.map((category) => (
              <button
                key={category}
                type="button"
                onClick={() => changeLibraryCategory(category)}
                className={`rounded-md border px-2 py-2 text-sm transition ${libraryCategory === category ? 'border-primary bg-primary text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
              >
                {KNOWLEDGE_LIBRARY_CATEGORY_LABELS[category].replace('库', '')}
              </button>
            ))}
          </div>

          <select
            className="w-full border rounded-md px-3 py-2 text-sm"
            value={businessType}
            onChange={(event) => changeBusinessType(event.target.value)}
          >
            {KNOWLEDGE_LIBRARY_TYPE_OPTIONS[libraryCategory].map((type) => (
              <option key={type} value={type}>{getKnowledgeBusinessTypeLabel(type)}</option>
            ))}
          </select>

          <Input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => handleFilesSelected(event.target.files)}
          />
          <Button
            type="button"
            variant="outline"
            className="w-full"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
          >
            选择资料文件
          </Button>
          <p className="text-xs text-gray-500">
            支持 PDF、DOCX、XLSX、TXT、MD、PNG、JPG、WEBP 等图片，可多选；多文件会分别生成知识资料。
          </p>
          {files.length > 0 && (
            <div className="rounded-lg border bg-gray-50">
              <div className="flex items-center justify-between px-3 py-2 border-b">
                <span className="text-xs font-medium text-gray-600">已选择 {files.length} 个文件</span>
                <button
                  type="button"
                  className="text-xs text-gray-500 hover:text-red-600"
                  onClick={clearSelectedFiles}
                  disabled={uploading}
                >
                  清空全部
                </button>
              </div>
              <div className="max-h-44 overflow-y-auto divide-y">
                {files.map((file) => {
                  const fileKey = getKnowledgeFileKey(file)
                  return (
                    <div key={fileKey} className="flex items-center justify-between gap-3 px-3 py-2">
                      <div className="min-w-0">
                        <p className="text-sm truncate">{file.name}</p>
                        <p className="text-xs text-gray-500">{formatKnowledgeFileSize(file.size)}</p>
                      </div>
                      <button
                        type="button"
                        className="shrink-0 rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600"
                        onClick={() => removeSelectedFile(fileKey)}
                        disabled={uploading}
                        title="移除文件"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
          {uploadProgressText && (
            <p className={`text-xs ${uploadProgressText.includes('失败') ? 'text-red-600' : 'text-blue-600'}`}>
              {uploadProgressText}
            </p>
          )}

          <div className="space-y-2">
            <p className="text-xs text-gray-500">适用流程</p>
            {renderFlowToggles(flows, toggleFlow)}
          </div>

          <Button className="w-full" onClick={handleUpload} disabled={uploading}>
            {uploading ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Upload className="h-4 w-4 mr-1" />}
            上传并索引
          </Button>
        </div>

        <div className="lg:col-span-2 bg-white border rounded-xl">
          <div className="p-4 border-b flex items-center justify-between gap-4">
            <div>
              <h2 className="font-medium">资料列表</h2>
              <p className="text-xs text-gray-500 mt-1">
                可用 {stats.usable} 份 · 失败 {stats.failed} 份 · 停用 {stats.disabled} 份 · 共 {docs.length} 份
              </p>
            </div>
            <div className="flex items-center gap-2">
              <select
                className="border rounded-md px-3 py-2 text-sm"
                value={categoryFilter}
                onChange={(event) => setCategoryFilter(event.target.value as 'all' | KnowledgeLibraryCategory)}
              >
                <option value="all">全部资料库</option>
                {CATEGORY_ORDER.map((category) => (
                  <option key={category} value={category}>{KNOWLEDGE_LIBRARY_CATEGORY_LABELS[category]}</option>
                ))}
              </select>
              <select
                className="border rounded-md px-3 py-2 text-sm"
                value={flowFilter}
                onChange={(event) => setFlowFilter(event.target.value)}
              >
                <option value="">全部流程</option>
                {FLOW_OPTIONS.map((flow) => <option key={flow.value} value={flow.value}>{flow.label}</option>)}
              </select>
            </div>
          </div>

          {loading ? (
            <div className="py-16 text-center text-gray-500">
              <Loader2 className="h-8 w-8 mx-auto mb-2 animate-spin" />
              加载中...
            </div>
          ) : docs.length === 0 ? (
            <div className="py-16 text-center text-gray-400">暂无知识资料</div>
          ) : (
            <div className="divide-y">
              {docs.map((doc) => {
                const unavailableReason = getKnowledgeUnavailableReason(doc)
                const statusText = canAutoUseKnowledgeDocument(doc)
                  ? '可用'
                  : unavailableReason || STATUS_LABELS[doc.processingStatus] || doc.processingStatus
                const statusClass = canAutoUseKnowledgeDocument(doc)
                  ? 'bg-emerald-100 text-emerald-800'
                  : doc.processingStatus === 'failed'
                    ? 'bg-red-100 text-red-800'
                    : 'bg-slate-100 text-slate-700'
                return (
                  <div key={doc.id} className="p-4">
                    <div className="flex items-center justify-between gap-4">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="font-medium text-sm truncate">{doc.title}</p>
                          <Badge variant="outline">{getKnowledgeBusinessTypeLabel(doc.businessType)}</Badge>
                          <Badge className={statusClass}>{statusText}</Badge>
                        </div>
                        <div className="flex gap-1.5 mt-2 flex-wrap">
                          {doc.applicableFlows.slice(0, 3).map((flow) => (
                            <Badge key={flow} variant="secondary">
                              {FLOW_OPTIONS.find((item) => item.value === flow)?.label || flow}
                            </Badge>
                          ))}
                          {doc.applicableFlows.length > 3 && (
                            <Badge variant="secondary">+{doc.applicableFlows.length - 3}</Badge>
                          )}
                        </div>
                        {doc.errorMessage && (
                          <div className="mt-2 text-xs text-red-600 flex items-start gap-1">
                            <AlertTriangle className="h-3.5 w-3.5 mt-0.5" />
                            {doc.errorMessage}
                          </div>
                        )}
                        {expandedDocIds.includes(doc.id) && (
                          <div className="mt-3 rounded-lg border bg-slate-50 p-3">
                            <div className="flex items-center justify-between mb-2">
                              <p className="text-xs font-medium text-slate-600">原子素材粒度</p>
                              {atomLoadingId === doc.id && <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" />}
                            </div>
                            {(atomMap[doc.id] || []).length === 0 && atomLoadingId !== doc.id ? (
                              <p className="text-xs text-slate-500">暂无原子素材，重索引后会按页生成可引用粒度。</p>
                            ) : (
                              <div className="space-y-2">
                                {(atomMap[doc.id] || []).map((atom) => (
                                  <div key={atom.id} className="rounded-md bg-white border px-3 py-2">
                                    <div className="flex items-center gap-2">
                                      <p className="text-sm font-medium truncate">{atom.title}</p>
                                      <Badge variant="outline">{getKnowledgeBusinessTypeLabel(atom.businessType)}</Badge>
                                      <Badge variant="secondary">
                                        {atom.pageStart && atom.pageEnd ? `第${atom.pageStart}-${atom.pageEnd}页` : '整份'}
                                      </Badge>
                                    </div>
                                    {atom.summary && (
                                      <p className="mt-1 text-xs text-slate-500 line-clamp-2">{atom.summary}</p>
                                    )}
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-2 shrink-0 justify-end">
                        <Button size="sm" variant="outline" onClick={() => toggleAtoms(doc)} disabled={doc.processingStatus !== 'completed'}>
                          {expandedDocIds.includes(doc.id) ? '收起粒度' : '查看粒度'}
                        </Button>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button size="sm" variant="ghost" className="px-2" aria-label={`更多操作：${doc.title}`}>
                              <MoreHorizontal className="h-4 w-4" />
                              <span className="sr-only">更多操作</span>
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-40">
                            <DropdownMenuItem
                              className="cursor-pointer"
                              onClick={() => handleProcess(doc)}
                              disabled={processingId === doc.id || mutatingId === doc.id}
                            >
                              {processingId === doc.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                              重索引
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              className="cursor-pointer"
                              onClick={() => openEditDialog(doc)}
                              disabled={mutatingId === doc.id}
                            >
                              <Pencil className="h-4 w-4" />
                              编辑
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              className="cursor-pointer"
                              onClick={() => handleToggleEnabled(doc)}
                              disabled={mutatingId === doc.id}
                            >
                              <Power className="h-4 w-4" />
                              {doc.enabled ? '停用' : '启用'}
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              className="cursor-pointer text-destructive focus:text-destructive"
                              onClick={() => handleDeleteDocument(doc)}
                              disabled={mutatingId === doc.id}
                            >
                              <Trash2 className="h-4 w-4" />
                              删除
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      <Dialog open={Boolean(editingDoc)} onOpenChange={(open) => { if (!open) { setEditingDoc(null); setEditForm(null) } }}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>编辑知识资料</DialogTitle>
            <DialogDescription>编辑标题、资料类型和适用流程。已引用到项目的快照不受影响。</DialogDescription>
          </DialogHeader>
          {editForm && (
            <div className="space-y-3">
              <Input value={editForm.title} onChange={(event) => setEditForm({ ...editForm, title: event.target.value })} placeholder="资料标题" />
              <select
                className="w-full border rounded-md px-3 py-2 text-sm"
                value={editForm.businessType}
                onChange={(event) => setEditForm({ ...editForm, businessType: event.target.value })}
              >
                {KNOWLEDGE_LIBRARY_TYPE_OPTIONS[getKnowledgeLibraryCategory({ businessType: editForm.businessType })].map((type) => (
                  <option key={type} value={type}>{getKnowledgeBusinessTypeLabel(type)}</option>
                ))}
              </select>
              <div className="space-y-2">
                <p className="text-xs text-gray-500">适用流程</p>
                {renderFlowToggles(editForm.flows, toggleEditFlow)}
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => { setEditingDoc(null); setEditForm(null) }}>取消</Button>
            <Button onClick={handleSaveEdit} disabled={savingEdit}>
              {savingEdit && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export default KnowledgeBase
