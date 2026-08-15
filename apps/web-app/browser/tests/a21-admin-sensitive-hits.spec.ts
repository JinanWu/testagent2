import { execFileSync } from 'node:child_process'
import { join } from 'node:path'
import { expect, test, type Page, type Response } from '@playwright/test'

function requiredEnvironment(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error('A21 browser environment authority unavailable')
  return value
}

const PASSWORD = requiredEnvironment('A21_BROWSER_PASSWORD')
const PHASE = requiredEnvironment('A21_BROWSER_PHASE')
const ENDPOINT_ID = 'endpoint-browser-a21'
const INVOCATION_ID = 'invocation-browser-a21'
const RAW_MARKERS = [
  `input${String.fromCharCode(64)}safe.invalid`,
  '0912-345-678',
  `4${'1'.repeat(15)}`,
  `arguments${String.fromCharCode(64)}safe.invalid`,
  `result${String.fromCharCode(64)}safe.invalid`,
]
const DETAIL_ROUTE = `/api/admin/endpoints/${ENDPOINT_ID}/invocations/${INVOCATION_ID}`

async function login(page: Page, username: string) {
  await page.getByLabel('帳號').fill(username)
  await page.getByLabel('密碼').fill(PASSWORD)
  await page.getByRole('button', { name: '登入', exact: true }).click()
  await expect(page.getByRole('heading', { name: '開始對話' })).toBeVisible()
}

function databaseCommand(code: string, ...arguments_: string[]): string {
  const python = requiredEnvironment('A21_BROWSER_PYTHON')
  const root = requiredEnvironment('A21_BROWSER_ROOT')
  return execFileSync(python, ['-c', code, join(root, 'published.sqlite3'), ...arguments_], {
    encoding: 'utf8', env: { ...process.env, PYTHONPATH: '' },
  }).trim()
}

function setDetailAuditFailure(enabled: boolean): void {
  const code = [
    'import sqlite3,sys',
    'connection=sqlite3.connect(sys.argv[1])',
    'enabled=sys.argv[2]=="on"',
    'connection.execute("DROP TRIGGER IF EXISTS browser_a21_fail_detail_audit")',
    'enabled and connection.execute("CREATE TRIGGER browser_a21_fail_detail_audit BEFORE INSERT ON audit_events WHEN NEW.action=\'audit.detail.view\' BEGIN SELECT RAISE(ABORT, \'blocked\'); END")',
    'connection.commit()',
    'connection.close()',
  ].join(';')
  databaseCommand(code, enabled ? 'on' : 'off')
}

function databaseCounts(): { hits: number; sensitiveAudits: number; detailViews: number } {
  const code = [
    'import json,sqlite3,sys',
    'connection=sqlite3.connect(sys.argv[1])',
    `invocation="${INVOCATION_ID}"`,
    'hits=connection.execute("SELECT count(*) FROM invocation_sensitive_hits WHERE invocation_id=?",(invocation,)).fetchone()[0]',
    'sensitive=connection.execute("SELECT count(*) FROM audit_events WHERE invocation_id=? AND action=\'published_api.sensitive_data_detected\'",(invocation,)).fetchone()[0]',
    'views=connection.execute("SELECT count(*) FROM audit_events WHERE invocation_id=? AND action=\'audit.detail.view\'",(invocation,)).fetchone()[0]',
    'connection.close()',
    'print(json.dumps([hits,sensitive,views]))',
  ].join(';')
  const [hits, sensitiveAudits, detailViews] = JSON.parse(databaseCommand(code)) as number[]
  return { hits, sensitiveAudits, detailViews }
}

function observeResponses(page: Page) {
  const consoleMessages: string[] = []
  const pageErrors: string[] = []
  const responseBodies: Promise<string>[] = []
  page.on('console', (message) => consoleMessages.push(message.text()))
  page.on('pageerror', (error) => pageErrors.push(error.message))
  page.on('response', (response: Response) => {
    if (response.url().startsWith(requiredEnvironment('A21_BROWSER_BASE_URL'))) {
      responseBodies.push(response.text().catch(() => ''))
    }
  })
  return { consoleMessages, pageErrors, responseBodies }
}

async function assertRawAbsent(page: Page, observed: ReturnType<typeof observeResponses>) {
  const state = await page.evaluate(async () => ({
    text: document.body.innerText,
    html: document.body.innerHTML,
    url: location.href,
    local: Object.entries(localStorage),
    session: Object.entries(sessionStorage),
    caches: await Promise.all((await caches.keys()).map(async (name) => {
      const cache = await caches.open(name)
      return Promise.all((await cache.keys()).map(async (request) => ({
        url: request.url, body: await (await cache.match(request))?.text(),
      })))
    })),
  }))
  const inspected = JSON.stringify([
    state, observed.consoleMessages, observed.pageErrors, await Promise.all(observed.responseBodies),
  ])
  if (RAW_MARKERS.some((marker) => inspected.includes(marker))) {
    throw new Error('raw browser state escaped safe detail boundary')
  }
}

async function openDetail(page: Page) {
  await page.getByRole('button', { name: '完整呼叫紀錄' }).click()
  await page.getByLabel('端點識別碼').fill(ENDPOINT_ID)
  await page.getByRole('button', { name: '查詢', exact: true }).click()
  const invocation = page.getByRole('button', { name: new RegExp(INVOCATION_ID) })
  await expect(invocation).toBeVisible()
  await invocation.click()
  await expect(page.getByRole('heading', { name: '呼叫詳情' })).toBeVisible()
}

async function assertFiveSafeTargets(page: Page) {
  const hits = page.getByLabel('敏感資料命中')
  await expect(hits).toBeVisible()
  for (const label of ['輸入', 'Metadata', '回應資料', '工具參數', '工具結果']) {
    await expect(hits.getByText(label, { exact: true })).toBeVisible()
  }
  for (const value of ['/contact', '/phone', '/answer', '/category', '/result/content',
    'tool-browser-a21-1', 'tool-browser-a21-2', '1–4', 'email_detector']) {
    await expect(hits).toContainText(value)
  }
  await expect(hits).toContainText('無資料')
  await expect(hits).not.toContainText(/raw|snippet|hash|audit[_ -]?id|稽核識別碼/i)
}

test('A21 production SPA canonical Admin sensitive-hit browser closure', async ({ browser }) => {
  if (PHASE === 'restart') {
    const context = await browser.newContext()
    const page = await context.newPage()
    const observed = observeResponses(page)
    await page.goto('/')
    await login(page, 'browser-admin-a21')
    await openDetail(page)
    await assertFiveSafeTargets(page)
    const counts = databaseCounts()
    expect(counts.hits).toBe(5)
    expect(counts.sensitiveAudits).toBe(counts.hits)
    await assertRawAbsent(page, observed)
    expect(observed.pageErrors).toEqual([])
    await context.close()
    return
  }

  const anonymous = await browser.newContext()
  const anonymousPage = await anonymous.newPage()
  const deepLink = await anonymousPage.goto('/admin/invocations')
  expect(deepLink?.status()).toBe(200)
  expect(deepLink?.headers()['cache-control']).toBe('no-store')
  await expect(anonymousPage.getByRole('heading', { name: '登入智慧工作空間' })).toBeVisible()
  expect((await anonymous.request.get(DETAIL_ROUTE)).status()).toBe(401)
  await anonymous.close()

  const member = await browser.newContext()
  const memberPage = await member.newPage()
  const memberAdminRequests: string[] = []
  memberPage.on('request', (request) => {
    if (request.url().includes('/api/admin/')) memberAdminRequests.push(request.url())
  })
  await memberPage.goto('/')
  await login(memberPage, 'browser-owner-a21')
  await expect(memberPage.getByRole('button', { name: '完整呼叫紀錄' })).toHaveCount(0)
  await expect(memberPage.getByLabel('敏感資料命中')).toHaveCount(0)
  expect((await member.request.get(DETAIL_ROUTE)).status()).toBe(403)
  expect(memberAdminRequests).toHaveLength(0)
  await member.close()

  const admin = await browser.newContext()
  const page = await admin.newPage()
  const observed = observeResponses(page)
  await page.goto('/')
  await login(page, 'browser-admin-a21')
  await page.getByRole('button', { name: '完整呼叫紀錄' }).click()
  await page.getByLabel('端點識別碼').fill(ENDPOINT_ID)
  await page.getByRole('button', { name: '查詢', exact: true }).click()
  const invocation = page.getByRole('button', { name: new RegExp(INVOCATION_ID) })
  await expect(invocation).toBeVisible()

  const viewsBeforeFailure = databaseCounts().detailViews
  setDetailAuditFailure(true)
  try {
    await invocation.click()
    await expect(page.getByRole('alert')).toHaveText('目前無法載入完整呼叫紀錄，請稍後再試。')
    await expect(page.getByRole('heading', { name: '呼叫詳情' })).toHaveCount(0)
    expect(databaseCounts().detailViews).toBe(viewsBeforeFailure)
  } finally {
    setDetailAuditFailure(false)
  }

  await invocation.click()
  await expect(page.getByRole('heading', { name: '呼叫詳情' })).toBeVisible()
  await assertFiveSafeTargets(page)
  const warning = page.getByLabel('執行事件')
  await expect(warning).toContainText('回應包含可能的敏感資料。')
  await expect(warning).not.toContainText(/email_detector|phone_detector|card_detector|\/contact|\/phone|\/answer|5 筆/)
  const notFound = await admin.request.get(`/api/admin/endpoints/${ENDPOINT_ID}/invocations/missing-a21`)
  expect(notFound.status()).toBe(404)

  const counts = databaseCounts()
  expect(counts.hits).toBe(5)
  expect(counts.sensitiveAudits).toBe(counts.hits)
  expect(counts.detailViews).toBeGreaterThan(viewsBeforeFailure)
  await assertRawAbsent(page, observed)
  expect(observed.pageErrors).toEqual([])
  await admin.close()
})
