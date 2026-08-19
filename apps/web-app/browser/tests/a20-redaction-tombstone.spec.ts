import { expect, test, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

function requiredEnvironment(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error('A20 browser environment authority unavailable')
  return value
}

const PHASE = requiredEnvironment('A20_BROWSER_PHASE')
const PASSWORD = requiredEnvironment('A20_BROWSER_PASSWORD')
const STATE_ROOT = requiredEnvironment('A20_BROWSER_ROOT')
const ENDPOINT = 'endpoint-browser-a20'
const INVOCATION = 'invocation-browser-a20'

async function login(page: Page): Promise<void> {
  await page.getByLabel('帳號').fill('browser-admin-a20')
  await page.getByLabel('密碼').fill(PASSWORD)
  await page.getByRole('button', { name: '登入', exact: true }).click()
  await expect(page.getByRole('heading', { name: '開始對話' })).toBeVisible()
}

async function openDetail(page: Page): Promise<void> {
  await page.getByRole('button', { name: '完整呼叫紀錄' }).click()
  await page.getByLabel('端點識別碼').fill(ENDPOINT)
  await page.getByRole('button', { name: '查詢', exact: true }).click()
  await page.getByRole('button', { name: new RegExp(INVOCATION) }).click()
  await expect(page.getByRole('heading', { name: '呼叫詳情' })).toBeVisible()
}

async function redact(page: Page, target: string, row: string): Promise<void> {
  await page.getByLabel('目標類型').selectOption(target)
  await page.getByLabel('目標資料列識別碼').fill(row)
  await page.getByLabel('JSON Pointer（空白代表整份文件）').fill('/payload/value')
  await page.getByLabel('遮蔽原因').fill('privacy request')
  await page.getByRole('button', { name: '準備不可逆遮蔽' }).click()
  const dialog = page.getByRole('dialog', { name: '確認永久遮蔽' })
  await expect(dialog).toBeVisible()
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === 'POST' &&
    response.url().endsWith(`/api/admin/published-endpoints/${ENDPOINT}/invocations/${INVOCATION}/redactions`),
  )
  await dialog.getByRole('button', { name: '確認永久遮蔽' }).click()
  const response = await responsePromise
  expect(response.status()).toBe(200)
  await expect(page.getByRole('heading', { name: '呼叫詳情' })).toBeVisible()
}

test('A20 canonical production browser與distinct-process restart closure', async ({ page }) => {
  const consoleMessages: string[] = []
  const pageErrors: string[] = []
  const posts: number[] = []
  const markerValues: string[] = []
  page.on('console', (message) => consoleMessages.push(message.text()))
  page.on('pageerror', (error) => pageErrors.push(error.message))
  page.on('response', (response) => {
    if (response.request().method() === 'POST' && response.url().includes('/redactions')) posts.push(response.status())
  })
  await page.goto('/')
  await login(page)
  await openDetail(page)

  if (PHASE === 'primary') {
    const input = JSON.parse(await page.getByLabel('輸入').locator('pre').innerText()) as { payload: { value: number[] } }
    const event = JSON.parse(await page.getByLabel('執行事件').locator('pre').innerText()) as { payload: { value: number[] } }
    const tool = JSON.parse(await page.getByLabel('safe_tool 結果').locator('pre').innerText()) as { payload: { value: number[] } }
    const markers = [input.payload.value, event.payload.value, tool.payload.value]
    expect(markers.every((marker) => marker.length === 4 && marker.every(
      (value) => Number.isSafeInteger(value) && value >= 10_000_000 && value < 100_000_000,
    ))).toBe(true)
    markerValues.push(...markers.flat().map(String))

    await redact(page, 'invocation_input', INVOCATION)
    await redact(page, 'run_event', 'event-browser-a20')
    await redact(page, 'tool_result', 'tool-browser-a20')
    expect(posts).toEqual([200, 200, 200])
    const body = await page.locator('body').innerText()
    expect(markers.some((marker) => marker.some((value) => body.includes(String(value))))).toBe(false)
    await expect(page.getByLabel('輸入')).toContainText('已遮蔽')
    await expect(page.getByLabel('執行事件')).toContainText('已遮蔽')
    await expect(page.getByLabel('safe_tool 結果')).toContainText('已遮蔽')
    for (const suffix of ['', '-wal', '-shm']) {
      const bytes = await readFile(join(STATE_ROOT, `published.sqlite3${suffix}`)).catch((error: NodeJS.ErrnoException) => {
        if (error.code === 'ENOENT') return Buffer.alloc(0)
        throw error
      })
      expect(markerValues.some((marker) => bytes.includes(Buffer.from(marker, 'ascii')))).toBe(false)
    }
  } else {
    expect(posts).toEqual([])
    await expect(page.getByLabel('遮蔽紀錄').locator('li')).toHaveCount(3)
    await expect(page.getByLabel('輸入')).toContainText('已遮蔽')
    await expect(page.getByLabel('執行事件')).toContainText('已遮蔽')
    await expect(page.getByLabel('safe_tool 結果')).toContainText('已遮蔽')
  }

  const inspected = await page.evaluate(() => JSON.stringify({
    url: location.href,
    local: Object.entries(localStorage),
    session: Object.entries(sessionStorage),
    cookies: document.cookie,
  }))
  expect(markerValues.some((marker) => inspected.includes(marker))).toBe(false)
  expect(markerValues.some((marker) => consoleMessages.join('\n').includes(marker))).toBe(false)
  expect(pageErrors).toEqual([])
})
