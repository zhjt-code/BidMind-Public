import React, { useEffect, useState } from 'react'
import { Node, mergeAttributes } from '@tiptap/core'
import {
  ReactNodeViewRenderer,
  NodeViewWrapper,
  type NodeViewProps,
} from '@tiptap/react'
import { X, ImageOff, Loader2 } from 'lucide-react'
import { writingService } from '@/services/writingApi'

/**
 * 素材图片节点扩展。
 *
 * 设计要点(与后端 Word/HTML 预览的图片识别契约对齐):
 * - 逻辑引用以 `data-image-id` 为唯一真源, HTML 中不内联鉴权 URL;
 * - 编辑器内通过带 token 的 blob 显示真实图片(图片接口走 Bearer 鉴权,
 *   原生 <img src> 无法携带 token);
 * - 序列化(getHTML)统一输出 <figure data-image-id="N"><figcaption>..</figcaption></figure>,
 *   保证 section_composer / doc_renderer / 预览内联三处识别一致。
 */

export interface MaterialImageOptions {
  projectId: number | null
}

const MaterialImageNodeView: React.FC<NodeViewProps> = ({
  node,
  editor,
  extension,
  deleteNode,
}) => {
  const imageId = node.attrs.imageId as number | null
  const caption = node.attrs.caption as string | null
  const projectId = (extension.options as MaterialImageOptions).projectId
  const [objectUrl, setObjectUrl] = useState<string>('')
  const [failed, setFailed] = useState(false)
  const editable = editor.isEditable

  useEffect(() => {
    if (!imageId || !projectId) {
      setFailed(true)
      return
    }
    let active = true
    let createdUrl = ''
    setFailed(false)
    writingService
      .fetchImageBlob(projectId, imageId)
      .then((blob) => {
        if (!active) return
        createdUrl = URL.createObjectURL(blob)
        setObjectUrl(createdUrl)
      })
      .catch(() => {
        if (active) setFailed(true)
      })
    return () => {
      active = false
      if (createdUrl) URL.revokeObjectURL(createdUrl)
    }
  }, [imageId, projectId])

  return (
    <NodeViewWrapper
      className="bid-material-image"
      data-image-id={imageId ?? undefined}
    >
      <figure
        contentEditable={false}
        className="group relative my-3 flex flex-col items-center select-none"
      >
        {editable && (
          <button
            type="button"
            onClick={() => deleteNode()}
            title="移除该图片"
            className="absolute top-1 right-1 z-10 rounded-full bg-black/50 p-1 text-white opacity-0 transition-opacity hover:bg-black/70 group-hover:opacity-100"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
        {failed ? (
          <div className="flex flex-col items-center gap-1 rounded border border-dashed border-muted-foreground/40 px-6 py-4 text-xs text-muted-foreground">
            <ImageOff className="h-5 w-5" />
            <span>图片加载失败{imageId ? ` (#${imageId})` : ''}</span>
          </div>
        ) : objectUrl ? (
          <img
            src={objectUrl}
            alt={caption ?? `image-${imageId ?? ''}`}
            className="max-w-full rounded border"
            style={{ maxHeight: 420 }}
          />
        ) : (
          <div className="flex items-center gap-2 rounded border border-dashed border-muted-foreground/40 px-6 py-4 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>加载图片中...</span>
          </div>
        )}
        {caption && (
          <figcaption className="mt-1 text-center text-xs text-muted-foreground">
            {caption}
          </figcaption>
        )}
      </figure>
    </NodeViewWrapper>
  )
}

export const MaterialImage = Node.create<MaterialImageOptions>({
  name: 'materialImage',
  group: 'block',
  atom: true,
  draggable: true,
  selectable: true,

  addOptions() {
    return { projectId: null }
  },

  addAttributes() {
    return {
      imageId: {
        default: null,
        parseHTML: (element) => {
          const direct = element.getAttribute('data-image-id')
          if (direct) return Number(direct)
          const src = element.getAttribute('src') || ''
          const matched = src.match(/\/images\/(\d+)/)
          return matched ? Number(matched[1]) : null
        },
        renderHTML: (attributes) =>
          attributes.imageId
            ? { 'data-image-id': String(attributes.imageId) }
            : {},
      },
      caption: {
        default: null,
        parseHTML: (element) => {
          const figcaption = element.querySelector?.('figcaption')
          if (figcaption?.textContent) return figcaption.textContent.trim()
          return (
            element.getAttribute('alt') ||
            element.getAttribute('data-caption') ||
            null
          )
        },
        // caption 不作为属性渲染, 由 renderHTML 输出独立的 <figcaption>
        renderHTML: () => ({}),
      },
    }
  },

  parseHTML() {
    return [
      { tag: 'figure[data-image-id]' },
      { tag: 'img[data-image-id]' },
      {
        // 兼容历史: 手动插入过的 <img src=".../images/{id}">
        tag: 'img[src]',
        getAttrs: (element) => {
          const src = (element as HTMLElement).getAttribute('src') || ''
          return /\/images\/\d+/.test(src) ? {} : false
        },
      },
    ]
  },

  renderHTML({ HTMLAttributes, node }) {
    // 始终输出 <figcaption>(即使为空): 后端 _insert_figure_images 的
    // figure 正则要求 figcaption 存在, 否则图片会被静默丢弃。
    const caption = (node.attrs.caption as string | null) || ''
    return ['figure', mergeAttributes(HTMLAttributes), ['figcaption', {}, caption]]
  },

  addNodeView() {
    return ReactNodeViewRenderer(MaterialImageNodeView)
  },
})

export default MaterialImage
