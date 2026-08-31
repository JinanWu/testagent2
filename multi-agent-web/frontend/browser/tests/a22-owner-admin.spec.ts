import { expect, test, type Page } from '@playwright/test'
import { parseEndpointDocs } from '../../src/api/endpoints'

function env(name: string): string { const value = process.env[name]; if (!value) throw new Error('A22 browser authority unavailable'); return value }
const PASSWORD = env('A22_BROWSER_PASSWORD')
const PHASE = env('A22_BROWSER_PHASE')
const RAW_MARKER = env('A22_BROWSER_RAW_MARKER')
const INVOCATION = 'invocation-browser-a22-a'

async function login(page: Page, username: string) {
  await page.getByLabel('帳號').fill(username)
  await page.getByLabel('密碼').fill(PASSWORD)
  await page.getByRole('button', { name: '登入', exact: true }).click()
  await expect(page.getByText('開始新的對話', { exact: true })).toBeVisible()
}

function observe(page: Page) {
  const consoleMessages: string[] = []; const pageErrors: string[] = []; const network: string[] = []
  page.on('console', message => consoleMessages.push(message.text()))
  page.on('pageerror', error => pageErrors.push(error.message))
  page.on('response', response => {
    const url = new URL(response.url())
    if (url.pathname.startsWith('/api/')) network.push(`${response.request().method()} ${url.pathname}${url.search} ${response.status()}`)
  })
  return { consoleMessages, pageErrors, network }
}

async function browserState(page: Page): Promise<string> {
  return JSON.stringify(await page.evaluate(async () => ({
    text: document.body.innerText, html: document.body.innerHTML, url: location.href,
    localStorage: Object.entries(localStorage), sessionStorage: Object.entries(sessionStorage),
    caches: await Promise.all((await caches.keys()).map(async name => {
      const cache = await caches.open(name)
      return Promise.all((await cache.keys()).map(async request => ({ url: request.url, body: await (await cache.match(request))?.text() })))
    })),
  })))
}

async function assertAbsent(page: Page, observed: ReturnType<typeof observe>, values: string[]) {
  const inspected = JSON.stringify([await browserState(page), observed.consoleMessages, observed.pageErrors])
  if (values.some(value => inspected.includes(value))) throw new Error('A22_SECRET_HYGIENE_FAILED')
  expect(observed.pageErrors).toEqual([])
}

async function openOwnerEndpoint(page: Page): Promise<string> {
  await page.getByRole('button', { name: '端點管理' }).click()
  await expect(page.getByRole('heading', { name: '端點管理' })).toBeVisible()
  await page.getByRole('button', { name: 'stable', exact: true }).click()
  await expect(page.getByRole('heading', { name: '端點詳情', level: 1 })).toBeVisible()
  const match = locationFromPage(page).match(/\/endpoints\/([A-Za-z0-9_-]{1,128})$/)
  if (!match) throw new Error('owner endpoint identity unavailable')
  return match[1]
}

function locationFromPage(page: Page): string { return new URL(page.url()).pathname }

async function openAdminDetail(page: Page, endpointId: string) {
  await page.getByRole('button', { name: '完整呼叫紀錄' }).click()
  await page.getByLabel('端點識別碼').fill(endpointId)
  await page.getByRole('button', { name: '查詢', exact: true }).click()
  await page.getByRole('button', { name: new RegExp(INVOCATION) }).click()
  await expect(page.getByRole('heading', { name: '呼叫詳情' })).toBeVisible()
}

async function redactInput(page: Page) {
  await page.getByLabel('目標類型').selectOption('invocation_input')
  await page.getByLabel('目標資料列識別碼').fill(INVOCATION)
  await page.getByLabel('JSON Pointer').fill('/secret')
  await page.getByLabel('遮蔽原因').fill('privacy request')
  await page.getByRole('button', { name: '準備不可逆遮蔽' }).click()
  const response = page.waitForResponse(item => item.request().method() === 'POST' && item.url().includes('/redactions'))
  await page.getByRole('dialog', { name: '確認永久遮蔽' }).getByRole('button', { name: '確認永久遮蔽' }).click()
  expect((await response).status()).toBe(200)
  await expect(page.getByLabel('輸入')).toContainText('已遮蔽')
}

test('A22 canonical Owner/Admin browser and restart closure', async ({ browser }) => {
  if (PHASE === 'planner') {
    const context = await browser.newContext(); const page = await context.newPage(); const observed = observe(page)
    await page.goto('/'); await login(page, 'browser-planner-a22')
    await page.getByRole('button', { name: '端點管理' }).click()
    await page.getByRole('button', { name: '建立端點' }).click()
    await expect(page.getByRole('heading', { name: '建立端點' })).toBeVisible()
    await page.getByLabel('需求').fill('Create a safe endpoint that returns a concise structured greeting.')
    await page.getByLabel('Response mode').selectOption('structured')
    await page.getByLabel(/stable — Stable browser live skill/).check()
    const draft = page.waitForResponse(item => item.request().method() === 'POST' && new URL(item.url()).pathname === '/api/published-endpoints/draft')
    await page.getByRole('button', { name: '建立 Draft' }).click()
    expect((await draft).status()).toBe(201)
    await expect(page.getByLabel('Draft confirmation')).toBeVisible()
    await page.getByLabel('Slug').fill('a22-live-planner')
    const publish = page.waitForResponse(item => item.request().method() === 'POST' && new URL(item.url()).pathname === '/api/published-endpoints')
    await page.getByRole('button', { name: '發布端點' }).click()
    expect((await publish).status()).toBe(201)
    await expect(page.getByRole('heading', { name: '端點發布完成' })).toBeVisible()
    expect(observed.pageErrors).toEqual([])
    await context.close()
    return
  }
  const ownerAContext = await browser.newContext(); const ownerA = await ownerAContext.newPage(); const ownerObserved = observe(ownerA)
  await ownerA.goto('/'); await login(ownerA, 'browser-owner-a')
  const endpointA = await openOwnerEndpoint(ownerA)

  if (PHASE === 'primary') {
    await ownerA.getByRole('tab', { name: '憑證' }).click()
    await ownerA.getByRole('button', { name: '建立新憑證' }).click()
    await ownerA.getByLabel('名稱').fill('browser-created')
    await ownerA.getByLabel('用途描述').fill('canonical browser')
    await ownerA.getByLabel('到期時間戳').fill(String(Math.floor(Date.now() / 1000) + 86400))
    await ownerA.getByLabel('IP 白名單').fill('127.0.0.1')
    await ownerA.getByLabel('Rate limit requests').fill('25')
    const created = ownerA.waitForResponse(item => item.request().method() === 'POST' && item.url().endsWith(`/api/published-endpoints/${endpointA}/credentials`))
    await ownerA.getByRole('button', { name: '建立 credential' }).click()
    expect((await created).status()).toBe(201)
    const key = await ownerA.getByLabel('一次性 API key').locator('pre').innerText()
    if (!/^pk_[A-Za-z0-9_-]{43}$/.test(key)) throw new Error('A22_ONE_TIME_KEY_FORMAT_INVALID')
    await ownerA.getByRole('button', { name: '已保存並清除' }).click()
    await expect(ownerA.getByLabel('一次性 API key')).toHaveCount(0)
    await assertAbsent(ownerA, ownerObserved, [key, RAW_MARKER])
  } else {
    await ownerA.getByRole('tab', { name: '憑證' }).click()
    await expect(ownerA.getByLabel('Credential safe summaries')).toContainText('browser-created')
    await expect(ownerA.getByLabel('Credential safe summaries')).toContainText('canonical browser')
    await expect(ownerA.getByLabel('一次性 API key')).toHaveCount(0)
  }

  const docsResponse = ownerA.waitForResponse(item =>
    item.request().method() === 'GET' && new URL(item.url()).pathname === `/api/published-endpoints/${endpointA}/docs`)
  await ownerA.getByRole('tab', { name: '文件' }).click()
  const docsNetworkResponse = await docsResponse
  expect(docsNetworkResponse.status()).toBe(200)
  const docsPayload = await docsNetworkResponse.json()
  expect(() => parseEndpointDocs(docsPayload)).not.toThrow()
  await expect(ownerA.getByRole('heading', { name: 'Docs' })).toBeVisible()
  await expect(ownerA.getByLabel('端點文件摘要')).toContainText('stable')
  await ownerA.getByRole('tab', { name: '監控' }).click(); await expect(ownerA.getByLabel('安全診斷紀錄')).toContainText(INVOCATION)
  const adminRequests: string[] = []
  ownerA.on('request', request => { if (request.url().includes('/api/admin/')) adminRequests.push(request.url()) })
  await ownerA.goto('/admin/invocations'); await expect(ownerA.getByText('開始新的對話', { exact: true })).toBeVisible(); expect(adminRequests).toEqual([])

  const ownerBContext = await browser.newContext(); const ownerB = await ownerBContext.newPage(); observe(ownerB)
  await ownerB.goto('/'); await login(ownerB, 'browser-owner-b'); await ownerB.goto(`/endpoints/${endpointA}`)
  await expect(ownerB.getByRole('alert')).toHaveText('找不到端點或無權存取。')
  await ownerBContext.close()

  const adminContext = await browser.newContext(); const admin = await adminContext.newPage(); const adminObserved = observe(admin)
  await admin.goto('/'); await login(admin, 'browser-admin-a22')
  await admin.getByRole('button', { name: '端點管理' }).click()
  await expect(admin.getByText('目前沒有端點。')).toBeVisible()
  await admin.getByRole('button', { name: '我的端點' }).click()
  await admin.getByRole('menuitemradio', { name: '所有端點' }).click()
  await expect(admin.getByRole('button', { name: 'stable', exact: true })).toBeVisible()
  await expect(admin.getByRole('button', { name: 'browser-a22-b', exact: true })).toBeVisible()
  await admin.getByRole('button', { name: '新增對話' }).click()
  await openAdminDetail(admin, endpointA)
  if (PHASE === 'primary') {
    const rawText = await admin.getByLabel('輸入').locator('pre').innerText()
    if (!rawText.includes(RAW_MARKER)) throw new Error('A22_RAW_FIXTURE_UNAVAILABLE')
    await redactInput(admin)
  } else {
    await expect(admin.getByLabel('遮蔽紀錄').locator('li')).toHaveCount(1)
    await expect(admin.getByLabel('輸入')).toContainText('已遮蔽')
  }
  await assertAbsent(admin, adminObserved, [RAW_MARKER])
  expect(adminObserved.network.every(entry => !entry.includes(RAW_MARKER))).toBe(true)
  await adminContext.close(); await ownerAContext.close()
})
