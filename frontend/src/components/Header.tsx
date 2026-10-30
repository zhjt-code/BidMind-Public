import React, { useState } from 'react'
import { Link, useLocation, useSearchParams, useNavigate } from 'react-router-dom'
import { useAuthStore, useMessageStore } from '@/stores'
import Logo from './Logo'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu'
import {
  House, ClipboardCheck, ShieldAlert, PenTool, BookOpen,
  HelpCircle, ChevronDown, LogOut, Server,
  Cog, KeyRound, Bell, Inbox,
  Megaphone, MessageSquare, Settings2,
} from 'lucide-react'
import type { MessageItem } from '@/services/messageApi'
import ChangePasswordDialog from './ChangePasswordDialog'
import { siteConfig } from '@/config/site'
import { APP_HOME, buildAppProjectListPath, VOLUNTARY_LOGOUT_PATH } from '@/config/routes'
import OpenWechatContact from '@/components/OpenWechatContact'

interface PrimaryNavItem {
  to: string
  typeParam?: string
  label: string
  icon: React.ElementType
}

// 不同消息类型的图标与配色
interface TypeIconConfig {
  Icon: React.ElementType
  colorClass: string
}

const TYPE_ICON_MAP: Record<string, TypeIconConfig> = {
  broadcast: { Icon: Megaphone, colorClass: 'text-amber-500' },
  feedback_reply: { Icon: MessageSquare, colorClass: 'text-blue-500' },
  system: { Icon: Settings2, colorClass: 'text-slate-500' },
}

const getTypeIcon = (msg: MessageItem): TypeIconConfig => {
  // 优先按 type 字段；refType 兼容旧数据
  return (
    TYPE_ICON_MAP[msg.type]
    ?? TYPE_ICON_MAP[msg.refType ?? '']
    ?? { Icon: Inbox, colorClass: 'text-gray-400' }
  )
}

const PRIMARY_NAV: PrimaryNavItem[] = [
  { to: APP_HOME, label: '控制台', icon: House },
  { to: buildAppProjectListPath('bid_writing'), typeParam: 'bid_writing', label: '标书编写', icon: PenTool },
  { to: buildAppProjectListPath('bid_review'), typeParam: 'bid_review', label: '投标审查', icon: ClipboardCheck },
  { to: buildAppProjectListPath('collusion_check'), typeParam: 'collusion_check', label: '串标审查', icon: ShieldAlert },
  { to: '/knowledge-base', label: '知识库', icon: BookOpen },
]

interface HeaderProps {
  onOpenHelp: () => void
  onOpenFeedback: () => void
}

const Header: React.FC<HeaderProps> = ({ onOpenHelp }) => {
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { user, logout } = useAuthStore()
  const { unreadCount, recent, fetchRecent, markRead, markAllRead } = useMessageStore()
  const [showChangePassword, setShowChangePassword] = useState(false)

  const isAdmin = user?.role === 'admin'

  const handleLogout = () => {
    useMessageStore.getState().reset()
    sessionStorage.removeItem('msg_toast_shown')
    logout()
    navigate(VOLUNTARY_LOGOUT_PATH)
  }

  const isPrimaryActive = (item: PrimaryNavItem): boolean => {
    const { pathname } = location
    const urlType = searchParams.get('type')

    if (item.to === '/knowledge-base') {
      return pathname.startsWith('/knowledge-base')
    }

    if (!item.typeParam) {
      return pathname === APP_HOME && !urlType
    }

    if (urlType === item.typeParam) return true

    if (item.typeParam === 'collusion_check') {
      return pathname.includes('/collusion/')
    }
    if (item.typeParam === 'bid_writing') {
      return pathname.includes('/writing/')
    }
    if (item.typeParam === 'bid_review') {
      return /^\/project\/\d+\//.test(pathname) && !pathname.includes('/collusion/') && !pathname.includes('/writing/')
    }
    return false
  }

  const isAdminActive = location.pathname.startsWith('/admin')

  // 一级导航：靠左 + 字重层级 + 选中圆角矩形阴影 + 贴底指示条
  const primaryNavClassName = (active: boolean): string =>
    `relative flex h-10 shrink-0 items-center gap-2 whitespace-nowrap rounded-lg border px-2.5 text-[15px] transition-all duration-200 xl:px-3 ${
      active
        ? 'border-primary/15 bg-primary/8 font-semibold text-primary shadow-xs'
        : 'border-transparent font-medium text-gray-700 hover:bg-gray-50 hover:text-gray-900'
    }`

  return (
    <>
      <div className="h-0.5 bg-linear-to-r from-primary via-blue-500 to-cyan-400" />
      <header className="bg-white border-b border-gray-200/80 sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center h-16">
            {/* Logo */}
            <Link to={APP_HOME} className="flex items-center shrink-0">
              <Logo size="md" />
            </Link>

            <div className="h-5 w-px bg-gray-200 mx-4" aria-hidden />

            {/* 主导航：品牌区经竖线分区后靠左，与右侧工具区拉开层级 */}
            <nav className="flex items-center h-full gap-1" aria-label="主导航">
              {PRIMARY_NAV.map(item => {
                const Icon = item.icon
                const active = isPrimaryActive(item)
                return (
                  <Link
                    key={item.to}
                    to={item.to}
                    title={item.label}
                    className={primaryNavClassName(active)}
                  >
                    <Icon className={`h-[18px] w-[18px] shrink-0 ${active ? 'opacity-100' : 'opacity-80'}`} />
                    <span className="hidden xl:inline">{item.label}</span>
                    {active && (
                      <span
                        aria-hidden
                        className="absolute -bottom-3 inset-x-3 h-[2.5px] bg-primary rounded-full"
                      />
                    )}
                  </Link>
                )
              })}

              {/* 管理面板入口 */}
              {isAdmin && (
                <Link
                  to="/admin"
                  title="管理"
                  className={primaryNavClassName(isAdminActive)}
                >
                  <Cog className={`h-[18px] w-[18px] shrink-0 ${isAdminActive ? 'opacity-100' : 'opacity-80'}`} />
                  <span className="hidden xl:inline">管理</span>
                  {isAdminActive && (
                    <span
                      aria-hidden
                      className="absolute -bottom-3 inset-x-3 h-[2.5px] bg-primary rounded-full"
                    />
                  )}
                </Link>
              )}
            </nav>

            <div className="flex-1" />

            <div className="h-5 w-px bg-gray-200 mx-3" />

            {/* 工具区 */}
            <div className="flex items-center gap-1 shrink-0">
              {/* 站内信铃铛：仅有未读时显示 */}
              {unreadCount > 0 && (
              <DropdownMenu onOpenChange={(open) => { if (open) fetchRecent() }}>
                <DropdownMenuTrigger asChild>
                  <button
                    className="relative flex items-center gap-1.5 px-2.5 py-1.5 text-sm text-gray-500 hover:text-gray-900 hover:bg-gray-50 rounded-md transition-colors"
                    title="未读消息"
                  >
                    <Bell className="h-4 w-4" />
                    <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-red-500 text-white text-[10px] font-semibold leading-none px-1">
                      {unreadCount > 99 ? '99+' : unreadCount}
                    </span>
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-96">
                  <div className="flex items-center justify-between px-3 py-2">
                    <span className="text-sm font-medium text-gray-900">消息中心</span>
                    {unreadCount > 0 && (
                      <button
                        onClick={(e) => { e.stopPropagation(); markAllRead() }}
                        className="text-xs text-primary hover:underline"
                      >
                        全部标记已读
                      </button>
                    )}
                  </div>
                  <DropdownMenuSeparator />
                  {recent.length === 0 ? (
                    <div className="px-3 py-6 text-center text-sm text-gray-400">暂无消息</div>
                  ) : (
                    <div className="max-h-80 overflow-y-auto">
                      {recent.map((msg) => {
                        const { Icon, colorClass } = getTypeIcon(msg)
                        return (
                          <DropdownMenuItem
                            key={msg.id}
                            className="cursor-pointer flex flex-col items-start gap-1 px-3 py-2.5"
                            onClick={() => {
                              if (!msg.isRead) markRead(msg)
                              navigate('/messages')
                            }}
                          >
                            <div className="flex items-center gap-2 w-full">
                              <Icon className={`h-3.5 w-3.5 shrink-0 ${colorClass}`} />
                              {!msg.isRead && (
                                <span className="w-1.5 h-1.5 rounded-full bg-primary shrink-0" />
                              )}
                              <span className={`text-sm truncate flex-1 ${msg.isRead ? 'text-gray-500' : 'text-gray-900 font-medium'}`}>
                                {msg.title}
                              </span>
                            </div>
                            <p className="text-xs text-gray-400 truncate w-full pl-5">
                              {msg.content.length > 60 ? msg.content.slice(0, 60) + '...' : msg.content}
                            </p>
                          </DropdownMenuItem>
                        )
                      })}
                    </div>
                  )}
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    className="cursor-pointer justify-center text-sm text-primary"
                    onClick={() => navigate('/messages')}
                  >
                    查看全部
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              )}

              <OpenWechatContact
                label={siteConfig.deployContact.headerLabel}
                title={siteConfig.deployContact.title}
                description={siteConfig.deployContact.description}
                className="flex items-center gap-1.5 px-2.5 py-1.5 text-sm text-gray-500 hover:text-gray-900 hover:bg-gray-50 rounded-md transition-colors"
                labelClassName="hidden sm:inline"
              >
                <Server className="h-4 w-4" aria-hidden="true" />
              </OpenWechatContact>

              <button
                onClick={onOpenHelp}
                className="flex items-center gap-1.5 px-2.5 py-1.5 text-sm text-gray-500 hover:text-gray-900 hover:bg-gray-50 rounded-md transition-colors"
              >
                <HelpCircle className="h-4 w-4" />
                <span className="hidden sm:inline">帮助</span>
              </button>

              {/* 用户菜单 */}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button className="flex items-center gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-gray-50 focus:outline-hidden">
                    <div className="w-7 h-7 rounded-full bg-linear-to-br from-primary to-blue-600 text-white flex items-center justify-center text-xs font-semibold">
                      {user?.username?.charAt(0).toUpperCase() || 'U'}
                    </div>
                    <span className="text-sm text-gray-700 font-medium max-w-[80px] truncate hidden sm:block">
                      {user?.username}
                    </span>
                    {isAdmin && (
                      <span className="hidden sm:inline-block px-1.5 py-0.5 text-[10px] font-medium bg-amber-50 text-amber-600 rounded border border-amber-200/80">
                        管理员
                      </span>
                    )}
                    <ChevronDown className="h-3.5 w-3.5 text-gray-400" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-52">
                  <DropdownMenuLabel className="font-normal">
                    <div className="flex flex-col space-y-1">
                      <p className="text-sm font-medium leading-none">{user?.username}</p>
                      <p className="text-xs leading-none text-muted-foreground">{user?.email}</p>
                    </div>
                  </DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem className="cursor-pointer" onClick={() => navigate('/messages')}>
                    <Inbox className="h-4 w-4 text-gray-400" />
                    消息中心
                    {unreadCount > 0 && (
                      <span className="ml-auto min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-red-500 text-white text-[10px] font-semibold leading-none px-1">
                        {unreadCount > 99 ? '99+' : unreadCount}
                      </span>
                    )}
                  </DropdownMenuItem>
                  <DropdownMenuItem className="cursor-pointer" onClick={() => setShowChangePassword(true)}>
                    <KeyRound className="h-4 w-4 text-gray-400" />
                    修改密码
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem className="cursor-pointer" onClick={handleLogout}>
                    <LogOut className="h-4 w-4 text-gray-400" />
                    退出登录
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </div>
      </header>
      <ChangePasswordDialog
        visible={showChangePassword}
        onClose={() => setShowChangePassword(false)}
      />
    </>
  )
}

export default Header
