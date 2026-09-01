import { expect, test, type BrowserContext, type Page, type Response } from '@playwright/test'

const passwordAuthority = process.env.A19_BROWSER_PASSWORD
if (!passwordAuthority) throw new Error('A19 browser password authority unavailable')
const PASSWORD: string = passwordAuthority
const RAW_MARKER = 'A19_BROWSER_RAW_MARKER'
const ENDPOINT_A = 'endpoint-browser-a19-a'
const ENDPOINT_B = 'endpoint-browser-a19-b'
const ENDPOINT_MISSING = 'endpoint-browser-a19-missing'

async function login(page: Page, username: string) {
  await page.getByLabel('帳號').fill(username)
  await page.getByLabel('密碼').fill(PASSWORD)
  await page.getByRole('button', { name: '登入', exact: true }).click()
  await expect(page.getByText('開始新的對話', { exact: true })).toBeVisible()
}

async function assertRawAbsent(page: Page, consoleMessages: string[], responseBodies: string[]) {
  const state = await page.evaluate(async () => ({
    visible: document.body.innerText,
    html: document.body.innerHTML,
    url: location.href,
    local: Object.entries(localStorage),
    session: Object.entries(sessionStorage),
    caches: await Promise.all((await caches.keys()).map(async (name) => {
      const cache = await caches.open(name)
      return { name, entries: await Promise.all((await cache.keys()).map(async (request) => {
        const response = await cache.match(request)
        if (!response) throw new Error('cache entry disappeared during inspection')
        return { url: request.url, body: await response.text() }
      })) }
    })),
  }))
  expect(JSON.stringify([state, consoleMessages, responseBodies])).not.toContain(RAW_MARKER)
}

function observeOwnerResponses(page: Page) {
  const requests: string[] = []
  const responses: string[] = []
  const bodies: Promise<string>[] = []
  page.on('request', (request) => {
    if (request.url().includes('/api/published-endpoints/')) {
      const url = new URL(request.url())
      requests.push(`${request.method()} ${url.pathname}${url.search}`)
    }
  })
  page.on('response', (response: Response) => {
    if (response.url().includes('/api/published-endpoints/')) {
      const url = new URL(response.url())
      responses.push(`${response.status()} ${url.pathname}${url.search}`)
      bodies.push(response.text())
    }
  })
  return { requests, responses, bodies }
}

function endpointResponses(responses: string[], endpointId: string): string[] {
  return responses.filter((entry) => entry.includes(`/api/published-endpoints/${endpointId}/`)).sort()
}

async function closeCleanly(context: BrowserContext) { await context.close() }

test('production SPA與canonical ASGI完成A19 two-owner zero-raw browser closure', async ({ browser }) => {
  const anonymous = await browser.newContext()
  const anonymousPage = await anonymous.newPage()
  const anonymousOwnerRequests: string[] = []
  anonymousPage.on('request', (request) => {
    if (request.url().includes('/api/published-endpoints/')) anonymousOwnerRequests.push(request.url())
  })
  const deepLink = await anonymousPage.goto(`/endpoints/${ENDPOINT_A}`)
  expect(deepLink?.status()).toBe(200)
  await expect(anonymousPage.getByRole('heading', { name: 'ColaX' })).toBeVisible()
  expect(anonymousOwnerRequests).toEqual([])
  await closeCleanly(anonymous)

  const ownerA = await browser.newContext()
  const pageA = await ownerA.newPage()
  const consoleA: string[] = []
  const errorsA: string[] = []
  pageA.on('console', (message) => consoleA.push(message.text()))
  pageA.on('pageerror', (error) => errorsA.push(error.message))
  const observedA = observeOwnerResponses(pageA)
  await pageA.goto('/')
  await login(pageA, 'browser-owner-a')
  await pageA.goto(`/endpoints/${ENDPOINT_A}`)
  await expect(pageA.getByRole('heading', { name: '端點詳情', level: 1 })).toBeVisible()
  await pageA.getByRole('tab', { name: '監控' }).click()
  await expect(pageA.getByLabel('端點指標')).toContainText('US$ 0.001')
  await expect(pageA.getByLabel('端點指標')).toContainText('safe_a')
  await expect(pageA.getByLabel('端點指標')).toContainText('終態數')
  await expect(pageA.getByLabel('端點指標')).toContainText('12 ms')
  await expect(pageA.getByLabel('端點指標')).toContainText('price-v1')
  await expect(pageA.getByLabel('端點指標')).toContainText('每日趨勢（UTC）')
  await expect(pageA.getByLabel('安全診斷紀錄')).toContainText('invocation-browser-a19-a')
  await expect(pageA.getByLabel('安全診斷紀錄')).toContainText('request-browser-a19-a')
  await expect(pageA.getByLabel('安全診斷紀錄')).toContainText('建立')
  await expect(pageA.getByLabel('安全診斷紀錄')).toContainText('完成')
  expect(observedA.requests).toEqual([
    `GET /api/published-endpoints/${ENDPOINT_A}`,
    `GET /api/published-endpoints/${ENDPOINT_A}/metrics?window_seconds=86400`,
    `GET /api/published-endpoints/${ENDPOINT_A}/diagnostics?window_seconds=86400&limit=50`,
  ])
  expect(endpointResponses(observedA.responses, ENDPOINT_A)).toEqual([
    `200 /api/published-endpoints/${ENDPOINT_A}/diagnostics?window_seconds=86400&limit=50`,
    `200 /api/published-endpoints/${ENDPOINT_A}/metrics?window_seconds=86400`,
  ])
  await assertRawAbsent(pageA, consoleA, await Promise.all(observedA.bodies))

  await pageA.goto(`/endpoints/${ENDPOINT_B}`)
  await expect(pageA.getByRole('alert')).toHaveText('找不到端點或無權存取。')
  expect(endpointResponses(observedA.responses, ENDPOINT_B)).toEqual([])
  await assertRawAbsent(pageA, consoleA, await Promise.all(observedA.bodies))
  await pageA.goto(`/endpoints/${ENDPOINT_MISSING}`)
  await expect(pageA.getByRole('alert')).toHaveText('找不到端點或無權存取。')
  expect(endpointResponses(observedA.responses, ENDPOINT_MISSING)).toEqual([])
  await assertRawAbsent(pageA, consoleA, await Promise.all(observedA.bodies))
  await pageA.goto('/')
  await expect(pageA.getByText('開始新的對話', { exact: true })).toBeVisible()
  await assertRawAbsent(pageA, consoleA, await Promise.all(observedA.bodies))

  await pageA.goto(`/endpoints/${ENDPOINT_A}`)
  await pageA.getByRole('tab', { name: '監控' }).click()
  await expect(pageA.getByLabel('安全診斷紀錄')).toContainText('invocation-browser-a19-a')
  await pageA.getByRole('button', { name: '登出' }).click()
  await expect(pageA.getByRole('heading', { name: 'ColaX' })).toBeVisible()
  await assertRawAbsent(pageA, consoleA, await Promise.all(observedA.bodies))
  expect(errorsA).toEqual([])
  await closeCleanly(ownerA)

  const ownerB = await browser.newContext()
  const pageB = await ownerB.newPage()
  const consoleB: string[] = []
  const errorsB: string[] = []
  pageB.on('console', (message) => consoleB.push(message.text()))
  pageB.on('pageerror', (error) => errorsB.push(error.message))
  const observedB = observeOwnerResponses(pageB)
  await pageB.goto('/')
  await login(pageB, 'browser-owner-b')
  await pageB.goto(`/endpoints/${ENDPOINT_B}`)
  await pageB.getByRole('tab', { name: '監控' }).click()
  await expect(pageB.getByLabel('端點指標')).toContainText('safe_b')
  await expect(pageB.getByLabel('安全診斷紀錄')).toContainText('invocation-browser-a19-b')
  expect(endpointResponses(observedB.responses, ENDPOINT_B)).toEqual([
    `200 /api/published-endpoints/${ENDPOINT_B}/diagnostics?window_seconds=86400&limit=50`,
    `200 /api/published-endpoints/${ENDPOINT_B}/metrics?window_seconds=86400`,
  ])
  await pageB.goto(`/endpoints/${ENDPOINT_A}`)
  await expect(pageB.getByRole('alert')).toHaveText('找不到端點或無權存取。')
  expect(endpointResponses(observedB.responses, ENDPOINT_A)).toEqual([])
  await assertRawAbsent(pageB, consoleB, await Promise.all(observedB.bodies))
  expect(errorsB).toEqual([])
  await closeCleanly(ownerB)
})
