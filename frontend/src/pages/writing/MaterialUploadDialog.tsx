import React, { useState, useRef, useCallback } from 'react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Upload,
  X,
  FileText,
  Image,
  Table,
  Loader2,
  Plus,
  CheckCircle2,
  XCircle,
  AlertCircle,
} from 'lucide-react'
import TruncatedHoverText from '@/components/upload/TruncatedHoverText'
import { writingService } from '@/services/writingApi'

interface FileItem {
  file: File
  status: 'pending' | 'uploading' | 'success' | 'failed'
  error?: string
}

interface MaterialUploadDialogProps {
  projectId: number
  open: boolean
  onOpenChange: (open: boolean) => void
  onUploadComplete: () => void
}

const formatFileSize = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const getFileIcon = (filename: string) => {
  const ext = filename.split('.').pop()?.toLowerCase() || ''
  if (['jpg', 'jpeg', 'png', 'bmp', 'gif'].includes(ext)) {
    return <Image className="h-4 w-4 text-green-600" />
  }
  if (['xlsx', 'xls', 'csv'].includes(ext)) {
    return <Table className="h-4 w-4 text-emerald-600" />
  }
  return <FileText className="h-4 w-4 text-blue-600" />
}

const getStatusIcon = (status: FileItem['status']) => {
  switch (status) {
    case 'uploading':
      return <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
    case 'success':
      return <CheckCircle2 className="h-4 w-4 text-green-500" />
    case 'failed':
      return <XCircle className="h-4 w-4 text-red-500" />
    default:
      return <AlertCircle className="h-4 w-4 text-muted-foreground" />
  }
}

const MaterialUploadDialog: React.FC<MaterialUploadDialogProps> = ({
  projectId,
  open,
  onOpenChange,
  onUploadComplete,
}) => {
  const [files, setFiles] = useState<FileItem[]>([])
  const [isUploading, setIsUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = e.target.files
    if (!selectedFiles || selectedFiles.length === 0) return

    const newFiles: FileItem[] = Array.from(selectedFiles).map((file) => ({
      file,
      status: 'pending',
    }))

    setFiles((prev) => [...prev, ...newFiles])

    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }, [])

  const handleRemoveFile = useCallback((index: number) => {
    if (isUploading) return
    setFiles((prev) => prev.filter((_, i) => i !== index))
  }, [isUploading])

  const handleUpload = useCallback(async () => {
    if (files.length === 0 || isUploading) return

    setIsUploading(true)
    let successCount = 0

    for (let i = 0; i < files.length; i++) {
      const item = files[i]
      if (item.status === 'success') {
        successCount++
        continue
      }

      setFiles((prev) =>
        prev.map((f, idx) =>
          idx === i ? { ...f, status: 'uploading' } : f
        )
      )

      try {
        await writingService.uploadMaterial(projectId, item.file)
        setFiles((prev) =>
          prev.map((f, idx) =>
            idx === i ? { ...f, status: 'success' } : f
          )
        )
        successCount++
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : '上传失败'
        setFiles((prev) =>
          prev.map((f, idx) =>
            idx === i ? { ...f, status: 'failed', error: errorMsg } : f
          )
        )
      }
    }

    setIsUploading(false)

    if (successCount > 0) {
      onUploadComplete()
      setFiles([])
      onOpenChange(false)
    }
  }, [files, isUploading, projectId, onUploadComplete, onOpenChange])

  const handleClose = useCallback(() => {
    if (isUploading) return
    setFiles([])
    onOpenChange(false)
  }, [isUploading, onOpenChange])

  const pendingCount = files.filter((f) => f.status === 'pending').length
  const successCount = files.filter((f) => f.status === 'success').length
  const failedCount = files.filter((f) => f.status === 'failed').length

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>上传素材文件</DialogTitle>
          <DialogDescription>
            选择素材文件后可预览列表，确认无误后点击"开始上传"
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-hidden flex flex-col min-h-0">
          {files.length === 0 ? (
            <div
              className="border-2 border-dashed rounded-lg p-8 text-center cursor-pointer hover:bg-muted/50 transition"
              onClick={() => fileInputRef.current?.click()}
            >
              <Upload className="h-10 w-10 mx-auto mb-3 text-muted-foreground" />
              <p className="text-sm text-muted-foreground mb-2">
                点击或拖拽文件到此处
              </p>
              <p className="text-xs text-muted-foreground">
                支持 PDF, DOCX, XLSX, JPG, PNG 等格式
              </p>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Badge variant="secondary">{files.length} 个文件</Badge>
                  {successCount > 0 && (
                    <Badge variant="default" className="bg-green-500">
                      {successCount} 已保存
                    </Badge>
                  )}
                  {failedCount > 0 && (
                    <Badge variant="destructive">{failedCount} 失败</Badge>
                  )}
                </div>
                {!isUploading && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <Plus className="h-4 w-4 mr-1" />
                    追加文件
                  </Button>
                )}
              </div>

              <div className="flex-1 overflow-y-auto border rounded-lg divide-y">
                {files.map((item, index) => (
                  <div
                    key={`${item.file.name}-${index}`}
                    className="flex items-center gap-3 p-3 hover:bg-muted/30 transition"
                  >
                    {getFileIcon(item.file.name)}
                    <div className="flex-1 min-w-0">
                      <TruncatedHoverText text={item.file.name} className="block text-sm font-medium truncate" />
                      <p className="text-xs text-muted-foreground">
                        {formatFileSize(item.file.size)}
                        {item.error && (
                          <span className="text-red-500 ml-2">
                            {item.error}
                          </span>
                        )}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      {getStatusIcon(item.status)}
                      {item.status === 'pending' && !isUploading && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-6 w-6 p-0"
                          onClick={() => handleRemoveFile(index)}
                        >
                          <X className="h-4 w-4 text-muted-foreground hover:text-destructive" />
                        </Button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.doc,.xlsx,.xls,.jpg,.jpeg,.png,.bmp"
          multiple
          className="hidden"
          onChange={handleFileSelect}
        />

        <DialogFooter className="mt-4">
          <Button
            variant="outline"
            onClick={handleClose}
            disabled={isUploading}
          >
            取消
          </Button>
          <Button
            onClick={handleUpload}
            disabled={files.length === 0 || isUploading || pendingCount === 0}
          >
            {isUploading ? (
              <>
                <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                保存中 ({successCount + failedCount}/{files.length})
              </>
            ) : (
              <>
                <Upload className="h-4 w-4 mr-1" />
                开始上传 ({pendingCount} 个文件)
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default MaterialUploadDialog
