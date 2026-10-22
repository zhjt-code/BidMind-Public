import React from 'react'
import { Loader2, CheckCircle2 } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { KnowledgeAtom, KnowledgeDocument } from '@/services/knowledgeBaseApi'
import {
  KNOWLEDGE_AUTHORITY_LABELS,
  KNOWLEDGE_LIBRARY_CATEGORY_LABELS,
  getKnowledgeBusinessTypeLabel,
  getKnowledgeLibraryCategory,
  getKnowledgeUnavailableReason,
  isKnowledgeDocumentExpired,
} from '@/services/knowledgeBaseView'

interface KnowledgeBaseDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  loading: boolean
  projecting: boolean
  docs: KnowledgeDocument[]
  atomMap: Record<number, KnowledgeAtom[]>
  selectedDocIds: number[]
  selectedAtomIds: number[]
  onToggleDoc: (docId: number) => void
  onToggleAtom: (atomId: number) => void
  onSubmit: () => void
}

export const KnowledgeBaseDialog: React.FC<KnowledgeBaseDialogProps> = ({
  open,
  onOpenChange,
  loading,
  projecting,
  docs,
  atomMap,
  selectedDocIds,
  selectedAtomIds,
  onToggleDoc,
  onToggleAtom,
  onSubmit,
}) => {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>从企业知识库引用素材</DialogTitle>
          <DialogDescription>
            选中的知识资料会快照复制到当前项目素材中，后续匹配、绑定、重试仍走现有写标链路。
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[420px] overflow-y-auto border rounded-lg divide-y">
          {loading ? (
            <div className="py-12 text-center text-gray-500">
              <Loader2 className="h-6 w-6 mx-auto mb-2 animate-spin" />
              加载知识库素材...
            </div>
          ) : docs.length === 0 ? (
            <div className="py-12 text-center text-gray-400">暂无已索引的写标知识资料</div>
          ) : (
            docs.map((doc) => {
              const selected = selectedDocIds.includes(doc.id)
              const expired = isKnowledgeDocumentExpired(doc)
              const atoms = atomMap[doc.id] || []
              return (
                <div
                  key={doc.id}
                  className={`w-full p-3 transition ${selected ? 'bg-primary/5' : 'hover:bg-muted/40'}`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate">{doc.title}</p>
                      <p className="text-xs text-gray-500 mt-1">
                        {doc.filename} · v{doc.version} · {doc.source || '未填写来源'}
                      </p>
                      <div className="flex gap-1.5 mt-2 flex-wrap">
                        <Badge variant="outline">
                          {doc.businessLabel || getKnowledgeBusinessTypeLabel(doc.businessType)}
                        </Badge>
                        <Badge variant="secondary">
                          {KNOWLEDGE_AUTHORITY_LABELS[doc.authority] || doc.authority}
                        </Badge>
                        <Badge variant="outline">
                          {KNOWLEDGE_LIBRARY_CATEGORY_LABELS[getKnowledgeLibraryCategory(doc)]}
                        </Badge>
                        {expired && <Badge className="bg-red-100 text-red-800">已过期</Badge>}
                        {getKnowledgeUnavailableReason(doc) && (
                          <Badge className="bg-slate-100 text-slate-700">
                            {getKnowledgeUnavailableReason(doc)}
                          </Badge>
                        )}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => onToggleDoc(doc.id)}
                      className={`h-5 w-5 rounded-full border flex items-center justify-center ${selected ? 'bg-primary border-primary' : 'bg-white'}`}
                      title="整份引用"
                    >
                      {selected && <CheckCircle2 className="h-4 w-4 text-white" />}
                    </button>
                  </div>
                  <div className="mt-3 rounded-lg bg-muted/40 border p-2">
                    <p className="text-xs text-muted-foreground mb-2">
                      原子素材：
                      {atoms.length > 0
                        ? '可单独选择引用，引用后仍需在绑定页确认'
                        : '暂无原子素材，可整份引用'}
                    </p>
                    <div className="space-y-1.5">
                      {atoms.map((atom) => {
                        const atomSelected = selectedAtomIds.includes(atom.id)
                        return (
                          <button
                            key={atom.id}
                            type="button"
                            onClick={() => onToggleAtom(atom.id)}
                            className={`w-full text-left rounded-md border px-2 py-1.5 text-xs ${atomSelected ? 'border-primary bg-primary/5' : 'bg-white hover:bg-muted'}`}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="font-medium truncate">{atom.title}</span>
                              <span className="text-muted-foreground shrink-0">
                                {atom.pageStart && atom.pageEnd
                                  ? `第${atom.pageStart}-${atom.pageEnd}页`
                                  : '整份'}
                              </span>
                            </div>
                            {atom.summary && (
                              <p className="mt-1 text-muted-foreground line-clamp-1">
                                {atom.summary}
                              </p>
                            )}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                </div>
              )
            })
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={onSubmit}
            disabled={projecting || (selectedDocIds.length === 0 && selectedAtomIds.length === 0)}
          >
            {projecting && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
            引用{' '}
            {selectedAtomIds.length > 0
              ? `${selectedAtomIds.length} 个原子素材`
              : `${selectedDocIds.length} 份`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
