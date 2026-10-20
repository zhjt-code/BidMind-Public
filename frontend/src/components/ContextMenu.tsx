import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export interface ContextMenuItem {
  key: string
  label: string
  disabled?: boolean
  danger?: boolean
  onClick?: () => void
}

interface ContextMenuProps {
  x: number
  y: number
  items: ContextMenuItem[]
  onClose: () => void
}

const MENU_WIDTH = 180

const ContextMenu: React.FC<ContextMenuProps> = ({ x, y, items, onClose }) => {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number }>({ left: x, top: y })

  useLayoutEffect(() => {
    const node = ref.current
    if (!node) return
    const { innerWidth, innerHeight } = window
    const rect = node.getBoundingClientRect()
    let left = x
    let top = y
    if (left + rect.width > innerWidth - 4) left = Math.max(4, innerWidth - rect.width - 4)
    if (top + rect.height > innerHeight - 4) top = Math.max(4, innerHeight - rect.height - 4)
    setPos({ left, top })
  }, [x, y])

  useEffect(() => {
    const handlePointer = (e: MouseEvent) => {
      if (ref.current && ref.current.contains(e.target as Node)) return
      onClose()
    }
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    const handleScrollOrResize = () => onClose()
    document.addEventListener('mousedown', handlePointer, true)
    document.addEventListener('contextmenu', handlePointer, true)
    document.addEventListener('keydown', handleKey)
    window.addEventListener('resize', handleScrollOrResize)
    window.addEventListener('scroll', handleScrollOrResize, true)
    return () => {
      document.removeEventListener('mousedown', handlePointer, true)
      document.removeEventListener('contextmenu', handlePointer, true)
      document.removeEventListener('keydown', handleKey)
      window.removeEventListener('resize', handleScrollOrResize)
      window.removeEventListener('scroll', handleScrollOrResize, true)
    }
  }, [onClose])

  const handleItemClick = (item: ContextMenuItem) => {
    if (item.disabled) return
    item.onClick?.()
    onClose()
  }

  return createPortal(
    <div
      ref={ref}
      role="menu"
      style={{ position: 'fixed', left: pos.left, top: pos.top, minWidth: MENU_WIDTH, zIndex: 9999 }}
      className="bg-white border border-gray-200 rounded-md shadow-lg py-1 select-none"
      onContextMenu={(e) => e.preventDefault()}
    >
      {items.map((it) => {
        const base = 'block w-full text-left px-3 py-1.5 text-sm transition-colors'
        const cls = it.disabled
          ? `${base} text-gray-300 cursor-not-allowed`
          : it.danger
            ? `${base} text-red-600 hover:bg-red-50 cursor-pointer`
            : `${base} text-gray-700 hover:bg-gray-50 cursor-pointer`
        return (
          <button
            key={it.key}
            type="button"
            disabled={it.disabled}
            onClick={() => handleItemClick(it)}
            className={cls}
          >
            {it.label}
          </button>
        )
      })}
    </div>,
    document.body
  )
}

export default ContextMenu
