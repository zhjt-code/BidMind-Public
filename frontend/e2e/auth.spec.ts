import { test, expect } from '@playwright/test'

const TEST_USER = {
  username: 'e2e_testuser',
  email: 'e2e_test@example.invalid',
  password: 'Test1234',
}
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD
if (!ADMIN_PASSWORD) throw new Error('E2E_ADMIN_PASSWORD is required')

test.describe('认证流程', () => {
  test('登录页密码可切换明文显示', async ({ page }) => {
    await page.goto('/login')
    const passwordInput = page.locator('input[name="password"]')

    await expect(passwordInput).toHaveAttribute('type', 'password')
    await page.fill('input[name="password"]', TEST_USER.password)
    await page.getByRole('button', { name: '显示密码' }).click()

    await expect(passwordInput).toHaveAttribute('type', 'text')
    await page.getByRole('button', { name: '隐藏密码' }).click()
    await expect(passwordInput).toHaveAttribute('type', 'password')
  })

  test('注册页密码与确认密码可切换明文显示', async ({ page }) => {
    await page.goto('/register')
    const passwordInput = page.locator('input[name="password"]')
    const confirmPasswordInput = page.locator('input[name="confirmPassword"]')

    await expect(passwordInput).toHaveAttribute('type', 'password')
    await expect(confirmPasswordInput).toHaveAttribute('type', 'password')

    await page.fill('input[name="password"]', TEST_USER.password)
    await page.fill('input[name="confirmPassword"]', TEST_USER.password)

    await page.getByRole('button', { name: '显示密码' }).click()
    await expect(passwordInput).toHaveAttribute('type', 'text')
    await expect(confirmPasswordInput).toHaveAttribute('type', 'password')

    await page.getByRole('button', { name: '显示确认密码' }).click()
    await expect(confirmPasswordInput).toHaveAttribute('type', 'text')

    await page.getByRole('button', { name: '隐藏密码' }).click()
    await expect(passwordInput).toHaveAttribute('type', 'password')
    await page.getByRole('button', { name: '隐藏确认密码' }).click()
    await expect(confirmPasswordInput).toHaveAttribute('type', 'password')
  })

  test('注册新用户', async ({ page }) => {
    await page.goto('/register')
    await page.fill('input[name="username"]', TEST_USER.username)
    await page.fill('input[name="email"]', TEST_USER.email)
    await page.fill('input[name="password"]', TEST_USER.password)
    await page.fill('input[name="confirmPassword"]', TEST_USER.password)
    await page.click('button[type="submit"]')

    await expect(page).toHaveURL('/', { timeout: 10_000 })
  })

  test('登录已有用户', async ({ page }) => {
    await page.goto('/login')
    await page.fill('input[name="username"]', 'admin')
    await page.fill('input[name="password"]', ADMIN_PASSWORD)
    await page.click('button[type="submit"]')

    await expect(page).toHaveURL('/', { timeout: 10_000 })
    await expect(page.locator('text=项目')).toBeVisible()
  })

  test('错误密码提示', async ({ page }) => {
    await page.goto('/login')
    await page.fill('input[name="username"]', 'admin')
    await page.fill('input[name="password"]', 'wrongpassword')
    await page.click('button[type="submit"]')

    await expect(page.locator('text=密码错误')).toBeVisible({ timeout: 5_000 })
  })

  test('未登录重定向到登录页', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/login/, { timeout: 5_000 })
  })
})
