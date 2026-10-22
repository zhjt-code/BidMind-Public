import React, { useState } from 'react'
import { authService } from '@/services/authApi'
import { toast } from 'sonner'
import { Modal } from '@/components'
import { useAuthStore } from '@/stores'

interface ChangePasswordDialogProps {
  visible: boolean
  onClose: () => void
}

interface FormState {
  currentPassword: string
  newPassword: string
  confirmPassword: string
}

interface PasswordFieldProps {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  placeholder: string
  autoComplete: string
}

const INITIAL_FORM: FormState = {
  currentPassword: '',
  newPassword: '',
  confirmPassword: '',
}

function validateForm(form: FormState): string | null {
  if (!form.currentPassword) return '请输入当前密码'
  if (!form.newPassword) return '请输入新密码'
  if (form.newPassword.length < 8) return '新密码至少8个字符'
  if (!(/[a-zA-Z]/.test(form.newPassword))) return '新密码必须包含字母'
  if (!(/\d/.test(form.newPassword))) return '新密码必须包含数字'
  if (form.newPassword !== form.confirmPassword) return '两次输入的新密码不一致'
  if (form.currentPassword === form.newPassword) return '新密码不能与当前密码相同'
  return null
}

const PasswordField: React.FC<PasswordFieldProps> = ({
  id,
  label,
  value,
  onChange,
  placeholder,
  autoComplete,
}) => {
  const [showPassword, setShowPassword] = useState(false)

  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-gray-700 mb-1">
        {label} <span className="text-red-500">*</span>
      </label>
      <div className="relative">
        <input
          id={id}
          type={showPassword ? 'text' : 'password'}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          className="w-full border rounded-lg px-3 py-2 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          autoComplete={autoComplete}
        />
        <button
          type="button"
          aria-label={showPassword ? '隐藏密码' : '显示密码'}
          title={showPassword ? '隐藏密码' : '显示密码'}
          onClick={() => setShowPassword(current => !current)}
          className="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-400 hover:text-slate-600 focus:outline-none focus:text-primary transition-colors"
        >
          {showPassword ? (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 3l18 18M10.58 10.58A2 2 0 0012 14a2 2 0 001.42-.58M9.88 4.24A9.53 9.53 0 0112 4c5.52 0 9 5.5 9 8a7.84 7.84 0 01-1.64 3.6M6.6 6.6C4.28 8.13 3 10.42 3 12c0 2.5 3.48 8 9 8a9.46 9.46 0 004.28-1.02" />
            </svg>
          ) : (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M2.25 12s3.75-7 9.75-7 9.75 7 9.75 7-3.75 7-9.75 7-9.75-7-9.75-7z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15a3 3 0 100-6 3 3 0 000 6z" />
            </svg>
          )}
        </button>
      </div>
    </div>
  )
}

const ChangePasswordDialog: React.FC<ChangePasswordDialogProps> = ({ visible, onClose }) => {
  const [form, setForm] = useState<FormState>(INITIAL_FORM)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleClose = () => {
    setForm(INITIAL_FORM)
    setError('')
    onClose()
  }

  const handleChange = (field: keyof FormState, value: string) => {
    setForm(prev => ({ ...prev, [field]: value }))
    if (error) setError('')
  }

  const handleSubmit = async () => {
    const validationError = validateForm(form)
    if (validationError) {
      setError(validationError)
      return
    }

    setSubmitting(true)
    setError('')

    try {
      const tokens = await authService.changePassword(form.currentPassword, form.newPassword, form.confirmPassword)
      // 改密后旧令牌已失效, 用后端重签的新令牌替换当前会话, 避免被自身 401 踢下线
      useAuthStore.getState().setTokens(tokens)
      toast.success('密码修改成功')
      handleClose()
    } catch (err) {
      const message = err instanceof Error ? err.message : '密码修改失败,请稍后重试'
      setError(message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      visible={visible}
      title="修改密码"
      onClose={submitting ? undefined : handleClose}
      maskClosable={!submitting}
      footer={
        <>
          <button
            onClick={handleClose}
            disabled={submitting}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 border border-gray-300 rounded-lg disabled:opacity-50"
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-600 disabled:opacity-50 flex items-center gap-2"
          >
            {submitting && (
              <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
            )}
            {submitting ? '提交中...' : '确认修改'}
          </button>
        </>
      }
    >
      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          {error}
        </div>
      )}
      <div key={visible ? 'open' : 'closed'} className="space-y-4">
        <PasswordField
          id="current-password"
          label="当前密码"
          value={form.currentPassword}
          onChange={value => handleChange('currentPassword', value)}
          placeholder="请输入当前密码"
          autoComplete="current-password"
        />
        <PasswordField
          id="new-password"
          label="新密码"
          value={form.newPassword}
          onChange={value => handleChange('newPassword', value)}
          placeholder="至少8位,需包含字母和数字"
          autoComplete="new-password"
        />
        <PasswordField
          id="confirm-password"
          label="确认新密码"
          value={form.confirmPassword}
          onChange={value => handleChange('confirmPassword', value)}
          placeholder="请再次输入新密码"
          autoComplete="new-password"
        />
        <p className="text-xs text-gray-400">密码要求: 至少8个字符,必须包含字母和数字</p>
      </div>
    </Modal>
  )
}

export default ChangePasswordDialog
