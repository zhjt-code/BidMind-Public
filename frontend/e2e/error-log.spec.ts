import { test, expect, type Page } from '@playwright/test'

const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD
if (!ADMIN_PASSWORD) throw new Error('E2E_ADMIN_PASSWORD is required')
const ADMIN = { username: 'admin', password: ADMIN_PASSWORD }

async function loginAndOpen(page: Page) {
  await page.goto('/login')
  await page.fill('input[name="username"]', ADMIN.username)
  await page.fill('input[name="password"]', ADMIN.password)
  await page.click('button[type="submit"]')
  await expect(page).toHaveURL('/', { timeout: 10_000 })
  await page.goto('/admin/error-logs')
  await expect(
    page.getByRole('heading', { name: '系统错误日志', exact: true }),
  ).toBeVisible({ timeout: 15_000 })
}

test.describe('错误日志页冒烟（Task 1 拆分特征锚点）', () => {
  test.beforeEach(async ({ page }) => {
    await loginAndOpen(page)
  })

  test('页面入口可访问且关键控件可见', async ({ page }) => {
    await expect(page.getByRole('button', { name: '抑制规则' })).toBeVisible()
    await expect(page.getByRole('button', { name: '批量管理' })).toBeVisible()
    await expect(page.getByRole('button', { name: '刷新分组' })).toBeVisible()
    await expect(page.getByTitle('分组视图')).toBeVisible()
    await expect(page.getByTitle('列表视图')).toBeVisible()
  })

  test('视图模式可切换（分组 ↔ 列表）', async ({ page }) => {
    await page.getByTitle('列表视图').click()
    await expect(page.getByTitle('按最新时间排序')).toBeHidden()
    await page.getByTitle('分组视图').click()
    await expect(page.getByTitle('按最新时间排序')).toBeVisible()
  })

  test('抑制规则管理弹窗可打开与关闭', async ({ page }) => {
    await page.getByRole('button', { name: '抑制规则' }).click()
    await expect(
      page.getByRole('heading', { name: '日志抑制规则管理', exact: true }),
    ).toBeVisible({ timeout: 5_000 })
    await page.keyboard.press('Escape')
    await expect(
      page.getByRole('heading', { name: '日志抑制规则管理', exact: true }),
    ).toBeHidden()
  })

  test('批量管理模式可进入与退出', async ({ page }) => {
    await page.getByRole('button', { name: '批量管理' }).click()
    await expect(page.getByRole('button', { name: '退出批量管理' })).toBeVisible()
    await page.getByRole('button', { name: '退出批量管理' }).click()
    await expect(page.getByRole('button', { name: '批量管理' })).toBeVisible()
  })

  // 防御测试: 重置筛选必须还原 sourceFilter
  test('重置筛选可还原 sourceFilter', async ({ page }) => {
    await expect(page.getByRole('button', { name: '重置筛选' })).toBeHidden()

    // 改变来源筛选 (默认 "全部来源" -> "接口服务")
    await page.getByRole('combobox').filter({ hasText: '全部来源' }).click()
    await page.getByRole('option', { name: '接口服务' }).click()
    await expect(page.getByRole('combobox').filter({ hasText: '接口服务' })).toBeVisible()
    await expect(page.getByRole('button', { name: '重置筛选' })).toBeVisible()

    // 重置 -> 来源 Select 回到 "全部来源", 重置按钮重新隐藏
    await page.getByRole('button', { name: '重置筛选' }).click()
    await expect(page.getByRole('combobox').filter({ hasText: '全部来源' })).toBeVisible()
    await expect(page.getByRole('button', { name: '重置筛选' })).toBeHidden()
  })
})
