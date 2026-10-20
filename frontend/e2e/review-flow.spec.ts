import { test, expect } from '@playwright/test'

const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD
if (!ADMIN_PASSWORD) throw new Error('E2E_ADMIN_PASSWORD is required')

test.describe('审查核心链路', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login')
    await page.fill('input[name="username"]', 'admin')
    await page.fill('input[name="password"]', ADMIN_PASSWORD)
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL('/', { timeout: 10_000 })
  })

  test('创建项目', async ({ page }) => {
    await page.click('text=新建项目')
    await page.fill('input[name="name"]', 'E2E测试项目')
    await page.click('button:has-text("创建")')

    await expect(page.locator('text=E2E测试项目')).toBeVisible({ timeout: 5_000 })
  })

  test('项目列表显示', async ({ page }) => {
    await expect(page.locator('table, [class*="project"]')).toBeVisible({ timeout: 5_000 })
  })

  test('管理员可访问设置页面', async ({ page }) => {
    await page.goto('/settings')
    await expect(page.locator('text=AI')).toBeVisible({ timeout: 5_000 })
  })
})
