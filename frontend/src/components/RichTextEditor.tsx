import React, { useEffect, useCallback, useState, useRef } from 'react'
import { useEditor, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Highlight from '@tiptap/extension-highlight'
import Placeholder from '@tiptap/extension-placeholder'
import Table from '@tiptap/extension-table'
import TableRow from '@tiptap/extension-table-row'
import TableCell from '@tiptap/extension-table-cell'
import TableHeader from '@tiptap/extension-table-header'
import { MaterialImage } from '@/components/editor/MaterialImageExtension'
import { SectionDiagram } from '@/components/editor/DiagramFigureExtension'
import { Button } from '@/components/ui/button'
import {
  Bold, List, ListOrdered,
  Undo2, Redo2, Table as TableIcon, ImagePlus,
} from 'lucide-react'

const BLOCK_TAG_RE = /<(?:p|h[1-6]|ul|ol|table|blockquote|div|figure|li|tr|td|th|hr|pre)[>\s/]/i

function ensureBlockStructure(html: string): string {
  if (!html || !html.trim()) return html
  if (BLOCK_TAG_RE.test(html)) return html
  return html
    .split(/\n+/)
    .filter(line => line.trim())
    .map(line => `<p>${line.trim()}</p>`)
    .join('')
}

export interface MaterialImageInfo {
  id: number
  caption: string | null
  imageType: string | null
}

interface RichTextEditorProps {
  content: string
  onChange: (html: string) => void
  editable?: boolean
  placeholder?: string
  projectId?: number
  images?: MaterialImageInfo[]
}

const RichTextEditor: React.FC<RichTextEditorProps> = ({
  content,
  onChange,
  editable = true,
  placeholder = '开始编辑...',
  projectId,
  images,
}) => {
  const [showImagePicker, setShowImagePicker] = useState(false)
  const imagePickerRef = useRef<HTMLDivElement>(null)

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [2, 3, 4] },
      }),
      Highlight,
      MaterialImage.configure({ projectId: projectId ?? null }),
      SectionDiagram.configure({ projectId: projectId ?? null }),
      Placeholder.configure({ placeholder }),
      Table.configure({ resizable: true }),
      TableRow,
      TableCell,
      TableHeader,
    ],
    content: ensureBlockStructure(content),
    editable,
    onUpdate: ({ editor: e }) => {
      onChange(e.getHTML())
    },
    editorProps: {
      attributes: {
        class: 'prose prose-sm max-w-none min-h-[500px] p-4 focus:outline-hidden',
      },
    },
  })

  useEffect(() => {
    if (editor && ensureBlockStructure(content) !== editor.getHTML()) {
      editor.commands.setContent(ensureBlockStructure(content), false)
    }
  }, [content, editor])

  useEffect(() => {
    // Tiptap setEditable 默认 emit update，挂载时会以当前(可能过期的)
    // 内容触发 onChange 并被自动保存写错章节；仅在值真正变化时调用且不发事件
    if (editor && editor.isEditable !== editable) {
      editor.setEditable(editable, false)
    }
  }, [editable, editor])

  useEffect(() => {
    if (!showImagePicker) return
    const handleClickOutside = (e: MouseEvent) => {
      if (imagePickerRef.current && !imagePickerRef.current.contains(e.target as Node)) {
        setShowImagePicker(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [showImagePicker])

  const insertTable = useCallback(() => {
    editor?.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()
  }, [editor])

  const insertImage = useCallback((img: MaterialImageInfo) => {
    if (!editor || !projectId) return
    editor.chain().focus().insertContent({
      type: 'materialImage',
      attrs: { imageId: img.id, caption: img.caption || `图片 #${img.id}` },
    }).run()
    setShowImagePicker(false)
  }, [editor, projectId])

  if (!editor) return null

  return (
    <div className="border rounded-lg overflow-hidden">
      {editable && (
        <div className="flex flex-wrap items-center gap-0.5 p-1.5 border-b bg-muted/30">
          <ToolbarButton
            onClick={() => editor.chain().focus().toggleBold().run()}
            active={editor.isActive('bold')}
            title="加粗"
          >
            <Bold className="h-4 w-4" />
          </ToolbarButton>

          <div className="w-px h-5 bg-border mx-1" />

          <ToolbarButton
            onClick={() => editor.chain().focus().toggleBulletList().run()}
            active={editor.isActive('bulletList')}
            title="无序列表"
          >
            <List className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => editor.chain().focus().toggleOrderedList().run()}
            active={editor.isActive('orderedList')}
            title="有序列表"
          >
            <ListOrdered className="h-4 w-4" />
          </ToolbarButton>

          <div className="w-px h-5 bg-border mx-1" />

          <ToolbarButton onClick={insertTable} title="插入表格">
            <TableIcon className="h-4 w-4" />
          </ToolbarButton>

          <div className="relative" ref={imagePickerRef}>
            <ToolbarButton
              onClick={() => setShowImagePicker(prev => !prev)}
              disabled={!images || images.length === 0}
              title={images && images.length > 0 ? `插入图片 (${images.length}张可用)` : '插入图片 (无可用图片)'}
            >
              <ImagePlus className="h-4 w-4" />
            </ToolbarButton>
            {showImagePicker && images && images.length > 0 && (
              <div className="absolute top-full left-0 mt-1 z-50 bg-popover border rounded-md shadow-lg p-2 min-w-[200px] max-w-[300px] max-h-[240px] overflow-auto">
                <div className="text-xs text-muted-foreground mb-1.5 px-1">选择要插入的图片</div>
                {images.map(img => (
                  <button
                    key={img.id}
                    className="w-full text-left text-sm px-2 py-1.5 rounded hover:bg-accent truncate block"
                    onClick={() => insertImage(img)}
                  >
                    {img.caption || `图片 #${img.id}`}
                    {img.imageType && (
                      <span className="ml-1 text-xs text-muted-foreground">({img.imageType})</span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="w-px h-5 bg-border mx-1" />

          <ToolbarButton
            onClick={() => editor.chain().focus().undo().run()}
            disabled={!editor.can().undo()}
            title="撤销"
          >
            <Undo2 className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            onClick={() => editor.chain().focus().redo().run()}
            disabled={!editor.can().redo()}
            title="重做"
          >
            <Redo2 className="h-4 w-4" />
          </ToolbarButton>
        </div>
      )}
      <EditorContent editor={editor} />
    </div>
  )
}

interface ToolbarButtonProps {
  onClick: () => void
  active?: boolean
  disabled?: boolean
  title?: string
  children: React.ReactNode
}

const ToolbarButton: React.FC<ToolbarButtonProps> = ({
  onClick, active, disabled, title, children,
}) => (
  <Button
    type="button"
    variant="ghost"
    size="sm"
    className={`h-7 w-7 p-0 ${active ? 'bg-muted text-foreground' : 'text-muted-foreground'}`}
    onClick={onClick}
    disabled={disabled}
    title={title}
  >
    {children}
  </Button>
)

export default RichTextEditor
