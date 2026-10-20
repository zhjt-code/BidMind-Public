import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { shouldShowWelcomeLetter } from '../src/components/welcomeLetterState.ts'

test('只有已加载且未确认的用户展示欢迎信', () => {
  assert.equal(shouldShowWelcomeLetter(null), false)
  assert.equal(shouldShowWelcomeLetter({}), true)
  assert.equal(shouldShowWelcomeLetter({ welcomeLetterAcknowledgedAt: null }), true)
  assert.equal(
    shouldShowWelcomeLetter({ welcomeLetterAcknowledgedAt: '2026-08-07T08:00:00Z' }),
    false,
  )
  // 空字符串不是有效确认时间，应保守展示
  assert.equal(shouldShowWelcomeLetter({ welcomeLetterAcknowledgedAt: '' }), true)
})

test('欢迎信保持全文手写版式和唯一确认出口', () => {
  const source = readFileSync(
    new URL('../src/components/WelcomeLetterDialog.tsx', import.meta.url),
    'utf8',
  )
  assert.match(source, /致敬奋斗者/)
  assert.match(source, /亲爱的奋斗者/)
  assert.match(source, /部分任务可能需要较长时间/)
  assert.match(source, /EMPHASIS_STYLE/, '关键句须走楷体合成加粗样式')
  assert.match(source, /WebkitTextStroke/, '合成加粗须含描边')
  assert.match(source, /textShadow/, '合成加粗须含阴影')
  assert.match(source, /style=\{EMPHASIS_STYLE\}/)
  assert.doesNotMatch(
    source,
    /部分任务可能需要较长时间[\s\S]{0,80}className="font-bold"/,
    '禁止仅靠 font-bold（楷体无字重）',
  )
  assert.match(source, /我已知晓，开始使用/)
  assert.match(source, /STKaiti|KaiTi/)
  assert.match(source, /onPointerDownOutside/)
  assert.match(source, /onEscapeKeyDown/)
  assert.match(source, /max-h-/)
  assert.doesNotMatch(source, /localStorage|sessionStorage|useFirstVisitDialog/)
  // 确认按钮必须携带 aria-busy 状态，向辅助技术播报提交进度
  assert.match(source, /aria-busy=\{submitting\}/, '确认按钮必须含 aria-busy={submitting}')
})

test('普通和管理员受保护路由共用欢迎信挂载点（精确顺序）', () => {
  const source = readFileSync(
    new URL('../src/components/ProtectedRoute.tsx', import.meta.url),
    'utf8',
  )

  // 三个早退出现的位置
  const authCheckedEarlyExitIdx = source.indexOf('!authChecked && isAuthenticated')
  const notAuthenticatedIdx = source.indexOf('!isAuthenticated')
  const requireAdminIdx = source.indexOf('requireAdmin && user')

  assert.ok(authCheckedEarlyExitIdx !== -1, '应含 authChecked 早退分支')
  assert.ok(notAuthenticatedIdx !== -1, '应含未登录早退分支')
  assert.ok(requireAdminIdx !== -1, '应含 requireAdmin 早退分支')

  // 最终 return 中三个节点的位置
  const heartbeatIdx = source.indexOf('<OnlinePresenceHeartbeat />')
  const dialogIdx = source.indexOf('<WelcomeLetterDialog />')
  const childrenIdx = source.indexOf('{children}')

  assert.ok(heartbeatIdx !== -1, '应含 OnlinePresenceHeartbeat')
  assert.ok(dialogIdx !== -1, '应含 WelcomeLetterDialog')
  assert.ok(childrenIdx !== -1, '应含 {children}')

  // 最终 return 中的顺序：Heartbeat → Dialog → children
  assert.ok(
    heartbeatIdx < dialogIdx,
    'OnlinePresenceHeartbeat 必须在 WelcomeLetterDialog 之前',
  )
  assert.ok(
    dialogIdx < childrenIdx,
    'WelcomeLetterDialog 必须在 {children} 之前',
  )

  // 三个早退均出现在最终 return 节点之前，确保挂载在门禁内侧
  assert.ok(
    authCheckedEarlyExitIdx < heartbeatIdx,
    'authChecked 早退必须在最终 return 之前',
  )
  assert.ok(
    notAuthenticatedIdx < heartbeatIdx,
    '未登录早退必须在最终 return 之前',
  )
  assert.ok(
    requireAdminIdx < heartbeatIdx,
    'requireAdmin 早退必须在最终 return 之前',
  )
})
