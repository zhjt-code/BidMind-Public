import React from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { FileText, ShieldAlert, PenTool, AlertTriangle } from 'lucide-react'
import type { Project } from '@/stores/projectStore'
import type { ProjectTypeSelection, CreateStep } from './types'

// ---------- 新建项目弹窗 ----------

interface CreateProjectDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  createStep: CreateStep
  selectedType: ProjectTypeSelection
  newProjectName: string
  onTypeSelect: (type: ProjectTypeSelection) => void
  onNameChange: (name: string) => void
  onBack: () => void
  onCreate: () => void
}

export const CreateProjectDialog: React.FC<CreateProjectDialogProps> = ({
  open,
  onOpenChange,
  createStep,
  selectedType,
  newProjectName,
  onTypeSelect,
  onNameChange,
  onBack,
  onCreate,
}) => {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {createStep === 'type' ? '新建项目' : '新建项目 -- 输入名称'}
          </DialogTitle>
          <DialogDescription>
            {createStep === 'type'
              ? '请选择要创建的项目类型'
              : '请输入项目名称以完成创建'}
          </DialogDescription>
        </DialogHeader>

        {createStep === 'type' ? (
          <div className="space-y-3">
            <button
              onClick={() => onTypeSelect('bid_writing')}
              className="w-full flex items-start gap-3 p-4 border-2 border-gray-200 rounded-lg hover:border-teal-400 hover:bg-teal-50 transition-colors text-left"
            >
              <div className="w-10 h-10 rounded-lg bg-teal-100 flex items-center justify-center shrink-0">
                <PenTool className="w-5 h-5 text-teal-600" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <p className="font-medium text-gray-900">标书编写</p>
                </div>
                <p className="text-xs text-gray-500 mt-0.5">基于招标文件自动生成投标文件，AI辅助编写技术方案</p>
              </div>
            </button>
            <button
              onClick={() => onTypeSelect('bid_review')}
              className="w-full flex items-start gap-3 p-4 border-2 border-gray-200 rounded-lg hover:border-primary hover:bg-blue-50 transition-colors text-left"
            >
              <div className="w-10 h-10 rounded-lg bg-blue-100 flex items-center justify-center shrink-0">
                <FileText className="w-5 h-5 text-blue-600" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <p className="font-medium text-gray-900">投标审查</p>
                </div>
                <p className="text-xs text-gray-500 mt-0.5">对标招标文件，逐项审查投标文件的响应情况</p>
              </div>
            </button>
            <button
              onClick={() => onTypeSelect('collusion_check')}
              className="w-full flex items-start gap-3 p-4 border-2 border-gray-200 rounded-lg hover:border-[#9056D8] hover:bg-[#F6F1FC] transition-colors text-left"
            >
              <div className="w-10 h-10 rounded-lg bg-[#F6F1FC] flex items-center justify-center shrink-0">
                <ShieldAlert className="w-5 h-5 text-[#9056D8]" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <p className="font-medium text-gray-900">串标审查</p>
                </div>
                <p className="text-xs text-gray-500 mt-0.5">多份投标文件交叉对比，识别潜在串通行为</p>
              </div>
            </button>
          </div>
        ) : (
          <div>
            <p className="text-sm text-gray-500 mb-3">
              类型：
              <Badge
                variant="outline"
                className={`ml-1 ${
                  selectedType === 'bid_writing'
                    ? 'bg-teal-50 text-teal-700 border-teal-200'
                    : selectedType === 'collusion_check'
                    ? 'bg-[#F6F1FC] text-[#6C40A2] border-[#E3D5F5]'
                    : 'bg-blue-50 text-blue-700 border-blue-200'
                }`}
              >
                {selectedType === 'bid_writing' ? '标书编写' : selectedType === 'collusion_check' ? '串标审查' : '投标审查'}
              </Badge>
            </p>
            <Input
              value={newProjectName}
              onChange={(e) => onNameChange(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && onCreate()}
              placeholder="请输入项目名称"
              autoFocus
            />
          </div>
        )}

        <DialogFooter>
          {createStep === 'type' ? (
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
          ) : (
            <>
              <Button variant="outline" onClick={onBack}>
                上一步
              </Button>
              <Button onClick={onCreate}>创建</Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---------- 同名确认弹窗 ----------

interface DuplicateConfirmDialogProps {
  duplicateName: string | null
  onCancel: () => void
  onConfirm: () => void
}

export const DuplicateConfirmDialog: React.FC<DuplicateConfirmDialogProps> = ({
  duplicateName,
  onCancel,
  onConfirm,
}) => {
  return (
    <Dialog open={!!duplicateName} onOpenChange={(open) => { if (!open) onCancel() }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>存在同名项目</DialogTitle>
        </DialogHeader>
        <div className="flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-yellow-500 shrink-0 mt-0.5" />
          <p className="text-gray-600 text-sm leading-relaxed">
            您已有名为「<span className="font-medium text-gray-900">{duplicateName}</span>」的项目。
            <br />
            确定仍要再创建一个同名项目吗？
          </p>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            取消
          </Button>
          <Button onClick={onConfirm}>仍要创建</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---------- 删除确认弹窗 ----------

interface DeleteConfirmDialogProps {
  project: Project | null
  loading?: boolean
  onCancel: () => void
  onConfirm: () => void
}

export const DeleteConfirmDialog: React.FC<DeleteConfirmDialogProps> = ({
  project,
  loading = false,
  onCancel,
  onConfirm,
}) => {
  return (
    <Dialog open={!!project} onOpenChange={(open) => { if (!open && !loading) onCancel() }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>确认删除</DialogTitle>
        </DialogHeader>
        <p className="text-gray-600">
          确定要删除项目{' '}
          <span className="font-medium text-gray-900">"{project?.name}"</span>{' '}
          吗？此操作不可恢复。
        </p>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={loading}>
            取消
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={loading}>
            {loading ? '删除中...' : '删除'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---------- 批量删除确认弹窗 ----------

interface BatchDeleteConfirmDialogProps {
  open: boolean
  selectedProjects: Project[]
  loading?: boolean
  onCancel: () => void
  onConfirm: () => void
}

export const BatchDeleteConfirmDialog: React.FC<BatchDeleteConfirmDialogProps> = ({
  open,
  selectedProjects,
  loading = false,
  onCancel,
  onConfirm,
}) => {
  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v && !loading) onCancel() }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{loading ? '批量删除中' : '确认批量删除'}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
            <p className="text-gray-600 text-sm leading-relaxed">
              {loading ? (
                <>
                  正在删除选中的{' '}
                  <span className="font-bold text-red-600">{selectedProjects.length}</span>{' '}
                  个项目，请稍候…
                </>
              ) : (
                <>
                  确定要删除以下{' '}
                  <span className="font-bold text-red-600">{selectedProjects.length}</span>{' '}
                  个项目吗？此操作不可恢复。
                </>
              )}
            </p>
          </div>
          <ul className="max-h-40 overflow-y-auto text-sm text-gray-500 space-y-1 pl-4 list-disc">
            {selectedProjects.map((p) => (
              <li key={p.id}>{p.name}</li>
            ))}
          </ul>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={loading}>
            取消
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={loading}>
            {loading ? '删除中...' : `确认删除 (${selectedProjects.length})`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
