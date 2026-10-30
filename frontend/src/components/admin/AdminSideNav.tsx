import React, { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  Settings, Users, AlertTriangle, ScrollText, MessageSquare, Cpu,
  Activity, FileSearch, ShieldAlert, PenLine,
  Megaphone, Pin, PinOff,
} from 'lucide-react'
import { isLlmCenterEnabled } from '@/config/features'

const HIDE_DELAY_MS = 700
const PIN_STORAGE_KEY = 'admin-sidenav-pinned'

interface NavGroup {
  title: string
  items: { to: string; label: string; icon: React.ElementType }[]
}

const NAV_GROUPS: NavGroup[] = [
  {
    title: '系统管理',
    items: [
      { to: '/admin/settings', label: 'AI设置', icon: Settings },
      { to: '/admin/llm-center', label: 'LLM Center', icon: Cpu },
      { to: '/admin/users', label: '用户管理', icon: Users },
    ],
  },
  {
    title: '运维监控',
    items: [
      { to: '/admin/ops', label: '运营总览', icon: Activity },
      { to: '/admin/ops/tender', label: '投标审查链路', icon: FileSearch },
      { to: '/admin/ops/collusion', label: '串标审查链路', icon: ShieldAlert },
      { to: '/admin/ops/writer', label: '标书编写链路', icon: PenLine },
      { to: '/admin/error-logs', label: '系统错误日志', icon: AlertTriangle },
      { to: '/admin/audit-logs', label: '关键操作审计', icon: ScrollText },
    ],
  },
  {
    title: '用户服务',
    items: [
      { to: '/admin/feedback', label: '反馈管理', icon: MessageSquare },
      { to: '/admin/broadcasts', label: '广播管理', icon: Megaphone },
    ],
  },
]

function readPinned(): boolean {
  try {
    return window.localStorage.getItem(PIN_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

function writePinned(pinned: boolean): void {
  try {
    window.localStorage.setItem(PIN_STORAGE_KEY, pinned ? '1' : '0')
  } catch {
    // localStorage 不可用时仅保持内存态
  }
}

interface NavBodyProps {
  pinned: boolean
  onTogglePin: () => void
}

const NavBody: React.FC<NavBodyProps> = ({ pinned, onTogglePin }) => {
  const location = useLocation()
  const visibleNavGroups = NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => isLlmCenterEnabled || item.to !== '/admin/llm-center'),
  })).filter(
    (group) => group.items.length > 0,
  )

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-gray-100 px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">
          管理导航
        </span>
        <button
          type="button"
          onClick={onTogglePin}
          className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-xs text-gray-500 hover:bg-gray-50 hover:text-gray-800"
          title={pinned ? '取消钉住（自动隐藏）' : '钉住侧栏'}
          aria-pressed={pinned}
        >
          {pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
          {pinned ? '取消钉住' : '钉住'}
        </button>
      </div>
      <nav className="flex-1 min-h-0 space-y-6 overflow-y-auto overscroll-contain px-3 py-4">
        {visibleNavGroups.map((group) => (
          <div key={group.title}>
            <h3 className="mb-2 px-3 text-xs font-semibold uppercase tracking-wider text-gray-400">
              {group.title}
            </h3>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const Icon = item.icon
                const active =
                  location.pathname === item.to
                  || (item.to !== '/admin/ops' && location.pathname.startsWith(`${item.to}/`))
                return (
                  <li key={item.to}>
                    <Link
                      to={item.to}
                      className={`flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                        active
                          ? 'bg-primary/8 text-primary'
                          : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                      }`}
                    >
                      <Icon className={`h-4 w-4 ${active ? 'text-primary' : 'text-gray-400'}`} />
                      {item.label}
                    </Link>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </nav>
    </div>
  )
}

export interface AdminSideNavProps {
  onPinnedChange?: (pinned: boolean) => void
}

const AdminSideNav: React.FC<AdminSideNavProps> = ({ onPinnedChange }) => {
  const [pinned, setPinned] = useState<boolean>(() => readPinned())
  const [expanded, setExpanded] = useState<boolean>(() => readPinned())
  const hideTimerRef = useRef<number | null>(null)

  const clearHideTimer = () => {
    if (hideTimerRef.current) {
      window.clearTimeout(hideTimerRef.current)
      hideTimerRef.current = null
    }
  }

  const showNav = () => {
    clearHideTimer()
    setExpanded(true)
  }

  const scheduleHideNav = () => {
    if (pinned) return
    clearHideTimer()
    hideTimerRef.current = window.setTimeout(() => {
      setExpanded(false)
      hideTimerRef.current = null
    }, HIDE_DELAY_MS)
  }

  const handleTogglePin = () => {
    setPinned((prev) => {
      const next = !prev
      writePinned(next)
      onPinnedChange?.(next)
      if (next) {
        clearHideTimer()
        setExpanded(true)
      } else {
        scheduleHideNav()
      }
      return next
    })
  }

  useEffect(() => {
    onPinnedChange?.(pinned)
  }, [pinned, onPinnedChange])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !pinned) {
        clearHideTimer()
        setExpanded(false)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      clearHideTimer()
    }
  }, [pinned])

  if (pinned) {
    return (
      <aside className="w-56 shrink-0 min-h-0 overflow-hidden border-r border-gray-200/80 bg-white">
        <NavBody pinned={pinned} onTogglePin={handleTogglePin} />
      </aside>
    )
  }

  return (
    <>
      <div
        className="relative w-3 shrink-0"
        onMouseEnter={showNav}
        onFocus={showNav}
      >
        <div
          className="absolute inset-y-0 left-0 z-30 w-3 cursor-pointer"
          tabIndex={0}
          aria-label="显示管理导航"
          onMouseEnter={showNav}
          onFocus={showNav}
        />
      </div>

      {expanded && (
        <div
          className="fixed inset-x-0 bottom-0 top-16 z-40"
          onMouseLeave={scheduleHideNav}
        >
          <div
            className="absolute inset-0 bg-black/10"
            onClick={() => {
              clearHideTimer()
              setExpanded(false)
            }}
          />
          <aside
            className="absolute inset-y-0 left-0 w-56 border-r border-gray-200/80 bg-white shadow-lg"
            onMouseEnter={showNav}
            onMouseLeave={scheduleHideNav}
            onFocus={showNav}
            onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget)) {
                scheduleHideNav()
              }
            }}
          >
            <NavBody pinned={pinned} onTogglePin={handleTogglePin} />
          </aside>
        </div>
      )}
    </>
  )
}

export default AdminSideNav
