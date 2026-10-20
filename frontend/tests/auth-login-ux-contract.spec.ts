/**
 * 职责: 锁定登录页 7 天免登录、页头 Logo 分隔线、改密显示密码；禁止密码框禁用粘贴
 * 不做: 不测真实登录、不测浏览器存储运行时
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

const loginSource = readSource('../src/pages/Login.tsx')
const headerSource = readSource('../src/components/public/PublicHeader.tsx')
const changePasswordSource = readSource('../src/components/ChangePasswordDialog.tsx')
const authStoreSource = readSource('../src/stores/authStore.ts')

test('公开首页 Logo 与导航之间有竖线分隔', () => {
  const logoIndex = headerSource.indexOf('<Logo size="md" />')
  const dividerIndex = headerSource.indexOf('w-px')
  const navIndex = headerSource.indexOf('aria-label="公开首页导航"')
  assert.ok(logoIndex >= 0 && dividerIndex > logoIndex && navIndex > dividerIndex)
  assert.match(headerSource, /hidden h-6 w-px[\s\S]*bg-slate-200 md:block/)
  assert.doesNotMatch(headerSource, /nav className="ml-10 /)
})

test('登录页「忘记密码」左侧提供默认未勾选的 7 天免登录', () => {
  assert.match(loginSource, /const \[rememberMe, setRememberMe\] = useState\(false\)/)
  assert.match(loginSource, /7天免登录/)
  assert.match(loginSource, /setTokens\(tokens, rememberMe\)/)
  assert.match(loginSource, /login\(user, tokens, rememberMe\)/)
  const rememberIndex = loginSource.indexOf('7天免登录')
  const forgotIndex = loginSource.indexOf('忘记密码？')
  assert.ok(rememberIndex >= 0 && forgotIndex > rememberIndex)
})

test('登录与改密密码框不禁用粘贴', () => {
  assert.doesNotMatch(loginSource, /onPaste/)
  assert.doesNotMatch(changePasswordSource, /onPaste/)
})

test('改密弹窗三个密码框都可显示/隐藏', () => {
  assert.match(changePasswordSource, /aria-label=\{showPassword \? '隐藏密码' : '显示密码'\}/)
  assert.match(changePasswordSource, /id="current-password"/)
  assert.match(changePasswordSource, /id="new-password"/)
  assert.match(changePasswordSource, /id="confirm-password"/)
})

test('认证持久化按 rememberMe 选择存储，登出清掉跨会话标记', () => {
  assert.match(authStoreSource, /createAuthWebStorage/)
  assert.match(authStoreSource, /rememberMe: rememberMe \?\? state\.rememberMe/)
  assert.match(authStoreSource, /rememberMe: false/)
})
