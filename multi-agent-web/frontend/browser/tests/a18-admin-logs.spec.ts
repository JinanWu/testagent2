import { execFileSync } from 'node:child_process'
import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

function requiredEnvironment(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error('browser environment authority unavailable')
  return value
}

const ADMIN_USERNAME = 'browser-admin'
const MEMBER_USERNAME = 'browser-member'
const PASSWORD = requiredEnvironment('A18_BROWSER_PASSWORD')
const ENDPOINT_ID = 'endpoint-browser-a18'
const INVOCATION_ID = 'invocation-browser-a18'
const RAW_MARKERS = [
  'BROWSER_RAW_INPUT_MARKER',
  'BROWSER_RAW_METADATA_MARKER',
  'BROWSER_RAW_OUTPUT_MARKER',
  'BROWSER_RAW_EVENT_MARKER',
]

async function login(page: Page, username: string) {
  await page.getByLabel('帳號').fill(username)
  await page.getByLabel('密碼').fill(PASSWORD)
  await page.getByRole('button', { name: '登入', exact: true }).click()
  await expect(page.getByText('開始新的對話', { exact: true })).toBeVisible()
}

async function assertRawAbsent(page: Page, consoleMessages: string[]) {
  const visible = await page.locator('body').innerText()
  const storage = await page.evaluate(() => ({
    local: Object.values(localStorage),
    session: Object.values(sessionStorage),
    url: location.href,
  }))
  const inspected = [visible, storage.url, ...storage.local, ...storage.session, ...consoleMessages]
  if (RAW_MARKERS.some((marker) => inspected.some((value) => value.includes(marker)))) {
    throw new Error('raw browser state was retained outside the detail view')
  }
}

function committedDetailAuditCount(): number {
  const python = process.env.A18_BROWSER_PYTHON
  const root = process.env.A18_BROWSER_ROOT
  if (!python || !root) throw new Error('browser audit authority unavailable')
  const code = [
    'import sqlite3,sys',
    'connection=sqlite3.connect(sys.argv[1])',
    'value=connection.execute("SELECT count(*) FROM audit_events WHERE action=?",("audit.detail.view",)).fetchone()[0]',
    'connection.close()',
    'print(value)',
  ].join(';')
  const output = execFileSync(python, ['-c', code, join(root, 'published.sqlite3')], {
    encoding: 'utf8',
    env: { ...process.env, PYTHONPATH: '' },
  })
  const count = Number(output.trim())
  if (!Number.isSafeInteger(count) || count < 0) throw new Error('browser audit result invalid')
  return count
}

test('production SPA與canonical ASGI完成Admin logs同源browser closure', async ({ browser }) => {
  const anonymous = await browser.newContext()
  const anonymousPage = await anonymous.newPage()
  const anonymousAdminRequests: string[] = []
  anonymousPage.on('request', (request) => {
    if (request.url().includes('/api/admin/')) anonymousAdminRequests.push(request.method())
  })
  const deepLinkResponse = await anonymousPage.goto('/admin/invocations')
  expect(deepLinkResponse?.status()).toBe(200)
  expect(deepLinkResponse?.headers()['cache-control']).toBe('no-store')
  expect(deepLinkResponse?.headers()['content-security-policy']).toContain("default-src 'self'")
  await expect(anonymousPage.getByRole('heading', { name: 'ColaX' })).toBeVisible()
  expect(anonymousAdminRequests).toEqual([])
  await anonymous.close()

  const member = await browser.newContext()
  const memberPage = await member.newPage()
  const memberAdminRequests: string[] = []
  memberPage.on('request', (request) => {
    if (request.url().includes('/api/admin/')) memberAdminRequests.push(request.method())
  })
  await memberPage.goto('/')
  await login(memberPage, MEMBER_USERNAME)
  await expect(memberPage.getByRole('button', { name: '完整呼叫紀錄' })).toHaveCount(0)
  await memberPage.goto('/admin/invocations')
  await expect(memberPage.getByText('開始新的對話', { exact: true })).toBeVisible()
  expect(memberAdminRequests).toEqual([])
  await memberPage.getByRole('button', { name: '登出' }).click()
  await expect(memberPage.getByRole('heading', { name: 'ColaX' })).toBeVisible()
  await member.close()

  const admin = await browser.newContext()
  const page = await admin.newPage()
  const consoleMessages: string[] = []
  const pageErrors: string[] = []
  const adminRequests: string[] = []
  page.on('console', (message) => consoleMessages.push(message.text()))
  page.on('pageerror', (error) => pageErrors.push(error.message))
  page.on('request', (request) => {
    if (request.url().includes('/api/admin/')) adminRequests.push(`${request.method()} ${new URL(request.url()).pathname}`)
  })
  await page.goto('/')
  await login(page, ADMIN_USERNAME)
  await page.getByRole('button', { name: '完整呼叫紀錄' }).click()
  await expect(page.getByRole('heading', { name: '完整呼叫紀錄' })).toBeVisible()
  await page.getByLabel('端點識別碼').fill(ENDPOINT_ID)
  await page.getByRole('button', { name: '查詢', exact: true }).click()
  const invocation = page.getByRole('button', { name: new RegExp(INVOCATION_ID) })
  await expect(invocation).toBeVisible()
  await invocation.click()
  await expect(page.getByRole('heading', { name: '呼叫詳情' })).toBeVisible()
  await expect(page.getByText(RAW_MARKERS[0])).toBeVisible()
  await expect(page.getByText(RAW_MARKERS[2])).toBeVisible()
  await expect(page.getByLabel('Metadata')).toContainText('已遮蔽')
  await expect(page.getByLabel('執行事件')).toContainText('已遮蔽')
  await expect(page.getByRole('heading', { name: '遮蔽紀錄', level: 2 })).toBeVisible()
  await expect(page.getByLabel('遮蔽紀錄')).toContainText('/trace')
  await expect(page.getByLabel('遮蔽紀錄')).toContainText('/state')
  const detailText = await page.locator('body').innerText()
  expect(detailText).not.toContain(RAW_MARKERS[1])
  expect(detailText).not.toContain(RAW_MARKERS[3])
  expect(detailText).not.toContain('$tombstone')
  expect(detailText).not.toContain('redaction_id')
  expect(detailText).not.toContain('redacted_at')
  expect(committedDetailAuditCount()).toBe(1)
  expect(adminRequests).toEqual([
    `GET /api/admin/endpoints/${ENDPOINT_ID}/invocations`,
    `GET /api/admin/endpoints/${ENDPOINT_ID}/invocations/${INVOCATION_ID}`,
  ])

  await page.getByRole('button', { name: '新增對話' }).click()
  await expect(page.getByText('開始新的對話', { exact: true })).toBeVisible()
  await assertRawAbsent(page, consoleMessages)

  await page.getByRole('button', { name: '完整呼叫紀錄' }).click()
  await page.getByLabel('端點識別碼').fill(ENDPOINT_ID)
  await page.getByRole('button', { name: '查詢', exact: true }).click()
  await page.getByRole('button', { name: new RegExp(INVOCATION_ID) }).click()
  await expect(page.getByRole('heading', { name: '呼叫詳情' })).toBeVisible()
  await page.getByRole('button', { name: '登出' }).click()
  await expect(page.getByRole('heading', { name: 'ColaX' })).toBeVisible()
  await assertRawAbsent(page, consoleMessages)
  expect(committedDetailAuditCount()).toBe(2)
  expect(pageErrors).toEqual([])
  await admin.close()
})
