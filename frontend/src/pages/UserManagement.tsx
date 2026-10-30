/**
 * 用户管理页面
 * 
 * 管理员专属页面，提供用户列表、启用/禁用、角色修改等功能。
 */

import React, { useEffect, useState, useCallback } from 'react'
import { toast } from 'sonner'
import { userService } from '@/services/userApi'
import { formatDate } from '@/lib/formatters'
import { User } from '@/stores'
import { Modal } from '@/components'
import UserOnlineStatsCard, { type UserOnlineStats } from './userManagement/UserOnlineStatsCard'

type UserListItem = User

interface PaginationInfo {
  page: number
  pageSize: number
  total: number
}

const UserManagementPage: React.FC = () => {
  const [users, setUsers] = useState<UserListItem[]>([])
  const [pagination, setPagination] = useState<PaginationInfo>({
    page: 1,
    pageSize: 10,
    total: 0,
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [onlineStats, setOnlineStats] = useState<UserOnlineStats | null>(null)
  const [onlineStatsLoading, setOnlineStatsLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState<string>('')
  const [statusFilter, setStatusFilter] = useState<string>('')
  
  // 操作确认对话框
  const [confirmDialog, setConfirmDialog] = useState<{
    type: 'status' | 'role' | 'delete'
    user: UserListItem
    newValue?: boolean | string
  } | null>(null)
  
  // 新建用户对话框
  const [createDialog, setCreateDialog] = useState(false)
  const [newUser, setNewUser] = useState({
    username: '',
    email: '',
    password: '',
    confirmPassword: '',
    role: 'user' as 'user' | 'admin'
  })
  const [createError, setCreateError] = useState('')
  const [creating, setCreating] = useState(false)

  // 重置密码对话框
  const [resetPasswordTarget, setResetPasswordTarget] = useState<UserListItem | null>(null)
  const [resetResult, setResetResult] = useState<{ username: string; password: string } | null>(null)
  const [resetting, setResetting] = useState(false)
  
  // 加载用户列表
  const loadUsers = useCallback(async () => {
    setLoading(true)
    setError(null)
    
    try {
      const result = await userService.getUsers({
        page: pagination.page,
        pageSize: pagination.pageSize,
        role: roleFilter || undefined,
        isActive: statusFilter === 'active' ? true : statusFilter === 'inactive' ? false : undefined,
        isOnline: statusFilter === 'online' ? true : undefined,
        search: search || undefined,
      })
      
      setUsers(result.items)
      setPagination(prev => ({
        ...prev,
        total: result.total,
      }))
    } catch (err) {
      const message = err instanceof Error ? err.message : '加载用户列表失败'
      setError(message)
    } finally {
      setLoading(false)
    }
  }, [pagination.page, pagination.pageSize, roleFilter, statusFilter, search])
  
  useEffect(() => {
    loadUsers()
  }, [loadUsers])

  const loadOnlineStats = useCallback(async (options: { silent?: boolean } = {}) => {
    if (!options.silent) {
      setOnlineStatsLoading(true)
    }

    try {
      const result = await userService.getOnlineStats()
      setOnlineStats(result)
    } catch (err) {
      if (!options.silent) {
        toast.error(err instanceof Error ? err.message : '加载在线用户统计失败')
      }
    } finally {
      if (!options.silent) {
        setOnlineStatsLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    void loadOnlineStats()
  }, [loadOnlineStats])

  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadOnlineStats({ silent: true })
    }, 60_000)

    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') {
        void loadOnlineStats({ silent: true })
      }
    }

    document.addEventListener('visibilitychange', refreshWhenVisible)

    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', refreshWhenVisible)
    }
  }, [loadOnlineStats])
  
  // 搜索防抖
  const [searchInput, setSearchInput] = useState('')
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput)
      setPagination(prev => ({ ...prev, page: 1 }))
    }, 500)
    return () => clearTimeout(timer)
  }, [searchInput])
  
  // 处理状态切换
  const handleStatusToggle = async (user: UserListItem) => {
    setConfirmDialog({
      type: 'status',
      user,
      newValue: !user.isActive,
    })
  }
  
  // 处理角色切换
  const handleRoleChange = async (user: UserListItem, newRole: 'user' | 'admin') => {
    if (user.role === newRole) return
    
    setConfirmDialog({
      type: 'role',
      user,
      newValue: newRole,
    })
  }
  
  // 处理删除
  const handleDelete = async (user: UserListItem) => {
    setConfirmDialog({
      type: 'delete',
      user,
    })
  }

  // 处理重置密码
  const handleResetPassword = (user: UserListItem) => {
    setResetPasswordTarget(user)
  }

  const confirmResetPassword = async () => {
    if (!resetPasswordTarget) return
    setResetting(true)
    try {
      const result = await userService.resetUserPassword(resetPasswordTarget.id)
      setResetResult({
        username: resetPasswordTarget.username,
        password: result.temporaryPassword,
      })
      setResetPasswordTarget(null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '重置密码失败')
      setResetPasswordTarget(null)
    } finally {
      setResetting(false)
    }
  }

  const handleCopyPassword = async () => {
    if (!resetResult) return
    try {
      await navigator.clipboard.writeText(resetResult.password)
      toast.success('临时密码已复制到剪贴板')
    } catch {
      toast.error('复制失败,请手动复制')
    }
  }
  
  // 确认操作
  const confirmAction = async () => {
    if (!confirmDialog) return
    
    const { type, user, newValue } = confirmDialog
    
    try {
      if (type === 'status') {
        await userService.updateUserStatus(user.id, newValue as boolean)
      } else if (type === 'role') {
        await userService.updateUserRole(user.id, newValue as 'user' | 'admin')
      } else if (type === 'delete') {
        await userService.deleteUser(user.id)
      }
      
      // 重新加载列表
      await loadUsers()
      await loadOnlineStats({ silent: true })
      setConfirmDialog(null)
      
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
    }
  }
  
  // 重置新建用户表单
  const resetCreateForm = () => {
    setNewUser({
      username: '',
      email: '',
      password: '',
      confirmPassword: '',
      role: 'user'
    })
    setCreateError('')
  }
  
  // 打开新建用户对话框
  const openCreateDialog = () => {
    resetCreateForm()
    setCreateDialog(true)
  }
  
  // 处理创建用户
  const handleCreateUser = async () => {
    // 表单验证
    if (!newUser.username.trim()) {
      setCreateError('请输入用户名')
      return
    }
    if (newUser.username.length < 3) {
      setCreateError('用户名至少3个字符')
      return
    }
    if (!newUser.email.trim()) {
      setCreateError('请输入邮箱')
      return
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(newUser.email)) {
      setCreateError('邮箱格式不正确')
      return
    }
    if (!newUser.password) {
      setCreateError('请输入密码')
      return
    }
    if (newUser.password.length < 8) {
      setCreateError('密码至少8个字符')
      return
    }
    if (newUser.password !== newUser.confirmPassword) {
      setCreateError('两次输入的密码不一致')
      return
    }
    
    setCreating(true)
    setCreateError('')
    
    try {
      await userService.createUser({
        username: newUser.username,
        email: newUser.email,
        password: newUser.password,
        role: newUser.role
      })
      
      // 关闭对话框并刷新列表
      setCreateDialog(false)
      resetCreateForm()
      await loadUsers()
      
    } catch (err) {
      const message = err instanceof Error ? err.message : '创建用户失败'
      setCreateError(message)
    } finally {
      setCreating(false)
    }
  }
  
  
  // 计算总页数
  const totalPages = Math.ceil(pagination.total / pagination.pageSize)
  
  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold text-gray-900">用户管理</h1>
        <button
          onClick={openCreateDialog}
          className="bg-primary text-white px-4 py-2 rounded-md hover:bg-blue-600 transition-colors flex items-center"
        >
          <svg className="w-5 h-5 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
          </svg>
          新建用户
        </button>
      </div>

      <UserOnlineStatsCard
        stats={onlineStats}
        loading={onlineStatsLoading}
        className="mb-6"
      />
      
      {/* 错误提示 */}
      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
          {error}
        </div>
      )}
      
      {/* 筛选栏 */}
      <div className="bg-white rounded-lg shadow-sm p-4 mb-6">
        <div className="flex flex-wrap gap-4">
          {/* 搜索框 */}
          <div className="flex-1 min-w-[200px]">
            <input
              type="text"
              placeholder="搜索用户名或邮箱..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="w-full border rounded-md px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary"
            />
          </div>
          
          {/* 角色筛选 */}
          <select
            value={roleFilter}
            onChange={(e) => {
              setRoleFilter(e.target.value)
              setPagination(prev => ({ ...prev, page: 1 }))
            }}
            className="border rounded-md px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary"
          >
            <option value="">全部角色</option>
            <option value="admin">管理员</option>
            <option value="user">普通用户</option>
          </select>
          
          {/* 状态筛选 */}
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value)
              setPagination(prev => ({ ...prev, page: 1 }))
            }}
            className="border rounded-md px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary"
          >
            <option value="">全部状态</option>
            <option value="active">已启用</option>
            <option value="inactive">已禁用</option>
            <option value="online">在线用户</option>
          </select>
        </div>
      </div>
      
      {/* 用户列表 */}
      <div className="bg-white rounded-lg shadow-sm overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-gray-500">
            <svg className="animate-spin h-8 w-8 mx-auto mb-2 text-primary" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            加载中...
          </div>
        ) : users.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            暂无用户数据
          </div>
        ) : (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  用户
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  角色
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  状态
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  最后登录
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  创建时间
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {users.map((user) => (
                <tr key={user.id} className="hover:bg-gray-50">
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex items-center">
                      <div className="w-10 h-10 rounded-full bg-primary text-white flex items-center justify-center text-sm font-medium">
                        {user.username.charAt(0).toUpperCase()}
                      </div>
                      <div className="ml-4">
                        <div className="text-sm font-medium text-gray-900">
                          {user.username}
                          {user.isOnline && (
                            <span className="ml-2 inline-flex items-center rounded-full bg-green-50 px-2 py-0.5 text-xs font-medium text-green-700">
                              在线
                            </span>
                          )}
                        </div>
                        <div className="text-sm text-gray-500">
                          {user.email}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <select
                      value={user.role}
                      onChange={(e) => handleRoleChange(user, e.target.value as 'user' | 'admin')}
                      className="text-sm border rounded px-2 py-1 focus:outline-hidden focus:ring-2 focus:ring-primary"
                    >
                      <option value="user">普通用户</option>
                      <option value="admin">管理员</option>
                    </select>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <button
                      onClick={() => handleStatusToggle(user)}
                      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                        user.isActive
                          ? 'bg-green-100 text-green-800 hover:bg-green-200'
                          : 'bg-red-100 text-red-800 hover:bg-red-200'
                      }`}
                    >
                      {user.isActive ? '已启用' : '已禁用'}
                    </button>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                    {formatDate(user.lastLogin)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                    {formatDate(user.createdAt)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium space-x-3">
                    <button
                      onClick={() => handleResetPassword(user)}
                      className="text-blue-600 hover:text-blue-900"
                    >
                      重置密码
                    </button>
                    <button
                      onClick={() => handleDelete(user)}
                      className="text-red-600 hover:text-red-900"
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        
        {/* 分页 */}
        {!loading && users.length > 0 && (
          <div className="bg-white px-4 py-3 flex items-center justify-between border-t border-gray-200 sm:px-6">
            <div className="flex-1 flex justify-between sm:hidden">
              <button
                onClick={() => setPagination(prev => ({ ...prev, page: prev.page - 1 }))}
                disabled={pagination.page <= 1}
                className="relative inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
              >
                上一页
              </button>
              <button
                onClick={() => setPagination(prev => ({ ...prev, page: prev.page + 1 }))}
                disabled={pagination.page >= totalPages}
                className="ml-3 relative inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
              >
                下一页
              </button>
            </div>
            <div className="hidden sm:flex-1 sm:flex sm:items-center sm:justify-between">
              <div>
                <p className="text-sm text-gray-700">
                  共 <span className="font-medium">{pagination.total}</span> 条记录，
                  第 <span className="font-medium">{pagination.page}</span> / <span className="font-medium">{totalPages}</span> 页
                </p>
              </div>
              <div className="flex space-x-2">
                <button
                  onClick={() => setPagination(prev => ({ ...prev, page: prev.page - 1 }))}
                  disabled={pagination.page <= 1}
                  className="relative inline-flex items-center px-3 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
                >
                  上一页
                </button>
                <button
                  onClick={() => setPagination(prev => ({ ...prev, page: prev.page + 1 }))}
                  disabled={pagination.page >= totalPages}
                  className="relative inline-flex items-center px-3 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
                >
                  下一页
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
      
      {/* 新建用户弹窗 */}
      <Modal
        visible={createDialog}
        title="新建用户"
        onClose={() => setCreateDialog(false)}
        maxWidth="max-w-lg"
        maskClosable={!creating}
        footer={
          <>
            <button
              onClick={() => setCreateDialog(false)}
              disabled={creating}
              className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 border border-gray-300 rounded-lg disabled:opacity-50"
            >
              取消
            </button>
            <button
              onClick={handleCreateUser}
              disabled={creating}
              className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-600 disabled:opacity-50 flex items-center gap-2"
            >
              {creating && (
                <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
              )}
              {creating ? '创建中...' : '创建用户'}
            </button>
          </>
        }
      >
        {createError && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
            {createError}
          </div>
        )}
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              用户名 <span className="text-red-500">*</span>
            </label>
            <input type="text" value={newUser.username}
              onChange={(e) => setNewUser(prev => ({ ...prev, username: e.target.value }))}
              placeholder="请输入用户名（至少3个字符）"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              邮箱 <span className="text-red-500">*</span>
            </label>
            <input type="email" value={newUser.email}
              onChange={(e) => setNewUser(prev => ({ ...prev, email: e.target.value }))}
              placeholder="请输入邮箱地址"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              密码 <span className="text-red-500">*</span>
            </label>
            <input type="password" value={newUser.password}
              onChange={(e) => setNewUser(prev => ({ ...prev, password: e.target.value }))}
              placeholder="请输入密码（至少8个字符）"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              确认密码 <span className="text-red-500">*</span>
            </label>
            <input type="password" value={newUser.confirmPassword}
              onChange={(e) => setNewUser(prev => ({ ...prev, confirmPassword: e.target.value }))}
              placeholder="请再次输入密码"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">角色</label>
            <select value={newUser.role}
              onChange={(e) => setNewUser(prev => ({ ...prev, role: e.target.value as 'user' | 'admin' }))}
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary">
              <option value="user">普通用户</option>
              <option value="admin">管理员</option>
            </select>
          </div>
        </div>
      </Modal>

      {/* 操作确认弹窗 */}
      <Modal
        visible={!!confirmDialog}
        title={
          confirmDialog?.type === 'status' ? '确认更改状态'
          : confirmDialog?.type === 'role' ? '确认更改角色'
          : '确认删除'
        }
        onClose={() => setConfirmDialog(null)}
        footer={
          <>
            <button
              onClick={() => setConfirmDialog(null)}
              className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 border border-gray-300 rounded-lg"
            >
              取消
            </button>
            <button
              onClick={confirmAction}
              className={`px-4 py-2 text-sm rounded-lg text-white ${
                confirmDialog?.type === 'delete'
                  ? 'bg-red-600 hover:bg-red-700'
                  : 'bg-primary hover:bg-blue-600'
              }`}
            >
              确认
            </button>
          </>
        }
      >
        <p className="text-gray-600">
          {confirmDialog?.type === 'status' && (
            <>
              确定要{confirmDialog.newValue ? '启用' : '禁用'}用户{' '}
              <span className="font-medium text-gray-900">"{confirmDialog.user.username}"</span> 吗？
            </>
          )}
          {confirmDialog?.type === 'role' && (
            <>
              确定要将用户 <span className="font-medium text-gray-900">"{confirmDialog.user.username}"</span>{' '}
              的角色更改为 {confirmDialog.newValue === 'admin' ? '管理员' : '普通用户'} 吗？
            </>
          )}
          {confirmDialog?.type === 'delete' && (
            <>
              确定要删除用户{' '}
              <span className="font-medium text-gray-900">"{confirmDialog.user.username}"</span> 吗？此操作不可恢复。
            </>
          )}
        </p>
      </Modal>

      {/* 重置密码确认弹窗 */}
      <Modal
        visible={!!resetPasswordTarget}
        title="重置用户密码"
        onClose={resetting ? undefined : () => setResetPasswordTarget(null)}
        maskClosable={!resetting}
        footer={
          <>
            <button
              onClick={() => setResetPasswordTarget(null)}
              disabled={resetting}
              className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 border border-gray-300 rounded-lg disabled:opacity-50"
            >
              取消
            </button>
            <button
              onClick={confirmResetPassword}
              disabled={resetting}
              className="px-4 py-2 text-sm bg-amber-600 hover:bg-amber-700 text-white rounded-lg disabled:opacity-50 flex items-center gap-2"
            >
              {resetting && (
                <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
              )}
              {resetting ? '重置中...' : '确认重置'}
            </button>
          </>
        }
      >
        <p className="text-gray-600">
          确定要重置用户{' '}
          <span className="font-medium text-gray-900">"{resetPasswordTarget?.username}"</span>{' '}
          的密码吗？系统将生成一个随机临时密码。
        </p>
      </Modal>

      {/* 临时密码展示弹窗 */}
      <Modal
        visible={!!resetResult}
        title="密码已重置"
        onClose={() => setResetResult(null)}
        footer={
          <button
            onClick={() => setResetResult(null)}
            className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-600"
          >
            我已记录,关闭
          </button>
        }
      >
        <div className="space-y-4">
          <div className="p-4 bg-green-50 border border-green-200 rounded-lg">
            <p className="text-sm text-green-800 mb-2">
              用户 <span className="font-semibold">{resetResult?.username}</span> 的密码已重置为:
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 px-3 py-2 bg-white border border-green-300 rounded text-base font-mono text-gray-900 select-all">
                {resetResult?.password}
              </code>
              <button
                onClick={handleCopyPassword}
                className="px-3 py-2 text-sm bg-green-600 hover:bg-green-700 text-white rounded-lg whitespace-nowrap"
              >
                复制
              </button>
            </div>
          </div>
          <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg">
            <p className="text-sm text-amber-700">
              请将此临时密码通过安全渠道告知用户,建议用户登录后立即修改密码。此密码仅显示一次,关闭后无法再次查看。
            </p>
          </div>
        </div>
      </Modal>
    </div>
  )
}

export default UserManagementPage
