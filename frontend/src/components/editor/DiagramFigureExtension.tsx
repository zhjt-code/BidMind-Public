import React, { useEffect, useRef, useState } from 'react'
import { Node, mergeAttributes } from '@tiptap/core'
import {
  NodeViewWrapper,
  ReactNodeViewRenderer,
  type NodeViewProps,
} from '@tiptap/react'
import { ImageOff, Loader2, Pencil, X } from 'lucide-react'
import { writingService } from '@/services/writingApi'
import DiagramEditDialog from './DiagramEditDialog'

/**
 * 章节生成图示节点扩展。
 *
 * - 逻辑引用以 `data-diagram-id` 为唯一真源;
 * - 编辑器内通过鉴权 Blob 显示后端渲染 PNG;
 * - 序列化保持 <figure data-diagram-id><figcaption>...</figcaption></figure>,
 *   让预览/Word 继续复用 figure_bridge 链路。
 */

export interface SectionDiagramOptions {
  projectId: number | null
}

const SectionDiagramNodeView: React.FC<NodeViewProps> = ({
  node,
  editor,
  extension,
  deleteNode,
  updateAttributes,
}) => {
  const diagramId = node.attrs.diagramId as number | null
  const caption = node.attrs.caption as string | null
  const projectId = (extension.options as SectionDiagramOptions).projectId
  const editable = editor.isEditable
  const [objectUrl, setObjectUrl] = useState('')
  const [failed, setFailed] = useState(false)
  const [editing, setEditing] = useState(false)
  const objectUrlRef = useRef('')

  useEffect(() => {
    if (!diagramId || !projectId) {
      setFailed(true)
      return
    }
    let active = true
    setFailed(false)
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current)
      objectUrlRef.current = ''
    }
    setObjectUrl('')
    writingService
      .fetchDiagramBlob(projectId, diagramId)
      .then((blob) => {
        if (!active) return
        const createdUrl = URL.createObjectURL(blob)
        objectUrlRef.current = createdUrl
        setObjectUrl(createdUrl)
      })
      .catch(() => {
        if (active) setFailed(true)
      })
    return () => {
      active = false
    }
  }, [diagramId, projectId])

  useEffect(() => {
    return () => {
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current)
    }
  }, [])

  const handleUpdated = async (title: string, contentSha256: string) => {
    updateAttributes({ caption: title })
    if (!diagramId || !projectId) return
    const blob = await writingService.fetchDiagramBlob(projectId, diagramId, contentSha256)
    const createdUrl = URL.createObjectURL(blob)
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current)
    objectUrlRef.current = createdUrl
    setObjectUrl(createdUrl)
    setFailed(false)
  }

  return (
    <NodeViewWrapper
      className="bid-section-diagram"
      data-diagram-id={diagramId ?? undefined}
    >
      <figure
        contentEditable={false}
        className="group relative my-3 flex flex-col items-center select-none"
      >
        {editable && (
          <div className="absolute right-1 top-1 z-10 flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
            <button
              type="button"
              onClick={() => setEditing(true)}
              title="编辑图表"
              className="rounded-full bg-black/50 p-1 text-white hover:bg-black/70"
            >
              <Pencil className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => deleteNode()}
              title="移除该图表"
              className="rounded-full bg-black/50 p-1 text-white hover:bg-black/70"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
        {failed ? (
          <div className="flex flex-col items-center gap-1 rounded border border-dashed border-muted-foreground/40 px-6 py-4 text-xs text-muted-foreground">
            <ImageOff className="h-5 w-5" />
            <span>图表加载失败{diagramId ? ` (#${diagramId})` : ''}</span>
          </div>
        ) : objectUrl ? (
          <img
            src={objectUrl}
            alt={caption ?? `diagram-${diagramId ?? ''}`}
            className="max-w-full rounded border bg-white"
            style={{ maxHeight: 420 }}
          />
        ) : (
          <div className="flex items-center gap-2 rounded border border-dashed border-muted-foreground/40 px-6 py-4 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>加载图表中...</span>
          </div>
        )}
        {caption && (
          <figcaption className="mt-1 text-center text-xs text-muted-foreground">
            {caption}
          </figcaption>
        )}
      </figure>
      {editable && diagramId && projectId && (
        <DiagramEditDialog
          open={editing}
          projectId={projectId}
          diagramId={diagramId}
          fallbackTitle={caption || `图表 #${diagramId}`}
          onOpenChange={setEditing}
          onUpdated={handleUpdated}
        />
      )}
    </NodeViewWrapper>
  )
}

export const SectionDiagram = Node.create<SectionDiagramOptions>({
  name: 'sectionDiagram',
  group: 'block',
  atom: true,
  draggable: true,
  selectable: true,

  addOptions() {
    return { projectId: null }
  },

  addAttributes() {
    return {
      diagramId: {
        default: null,
        parseHTML: (element) => {
          const direct = element.getAttribute('data-diagram-id')
          return direct ? Number(direct) : null
        },
        renderHTML: (attributes) =>
          attributes.diagramId
            ? { 'data-diagram-id': String(attributes.diagramId) }
            : {},
      },
      caption: {
        default: null,
        parseHTML: (element) => {
          const figcaption = element.querySelector?.('figcaption')
          if (figcaption?.textContent) return figcaption.textContent.trim()
          return element.getAttribute('data-caption') || null
        },
        renderHTML: () => ({}),
      },
    }
  },

  parseHTML() {
    return [{ tag: 'figure[data-diagram-id]' }]
  },

  renderHTML({ HTMLAttributes, node }) {
    const caption = (node.attrs.caption as string | null) || ''
    return ['figure', mergeAttributes(HTMLAttributes), ['figcaption', {}, caption]]
  },

  addNodeView() {
    return ReactNodeViewRenderer(SectionDiagramNodeView)
  },
})

export default SectionDiagram
