import { test, expect } from '@playwright/test'

const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD
if (!ADMIN_PASSWORD) throw new Error('E2E_ADMIN_PASSWORD is required')

test.describe('管理员功能', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login')
    await page.fill('input[name="username"]', 'admin')
    await page.fill('input[name="password"]', ADMIN_PASSWORD)
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL('/', { timeout: 10_000 })
  })

  test('用户管理页面可访问', async ({ page }) => {
    await page.goto('/admin/users')
    await expect(
      page.getByRole('heading', { name: '用户管理', exact: true }),
    ).toBeVisible({ timeout: 10_000 })
  })

  test('反馈管理页面可访问', async ({ page }) => {
    await page.goto('/admin/feedback')
    await expect(
      page.getByRole('heading', { name: '反馈管理', exact: true }),
    ).toBeVisible({ timeout: 10_000 })
  })

  test('审计日志页面可访问', async ({ page }) => {
    await page.goto('/admin/audit-logs')
    await expect(
      page.getByRole('heading', { name: '关键操作审计', exact: true }),
    ).toBeVisible({ timeout: 10_000 })
  })
})
