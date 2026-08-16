import { execFileSync } from 'node:child_process'
import { join } from 'node:path'
import { expect, test, type Page, type Response } from '@playwright/test'

function requiredEnvironment(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error('A21 browser environment authority unavailable')
  return value
}

const PASSWORD = requiredEnvironment('A21_BROWSER_PASSWORD')
const API_KEY = requiredEnvironment('A21_BROWSER_API_KEY')
const PHASE = requiredEnvironment('A21_BROWSER_PHASE')
const SLUG = 'stable'
const WARNING = { code: 'sensitive_data_detected', message: '回應包含可能的敏感資料。' }
const RAW_MARKERS = [
  `input${String.fromCharCode(64)}safe.invalid`,
  '0912-345-678',
  `4${'1'.repeat(15)}`,
  `arguments${String.fromCharCode(64)}safe.invalid`,
  `result${String.fromCharCode(64)}safe.invalid`,
  API_KEY,
]

async function login(page: Page, username: string, password = PASSWORD) {
  await page.getByLabel('帳號').fill(username)
  await page.getByLabel('密碼').fill(password)
  await page.getByRole('button', { name: '登入', exact: true }).click()
  await expect(page.getByRole('heading', { name: '開始對話' })).toBeVisible()
}

function databaseCommand(code: string, ...arguments_: string[]): string {
  const python = requiredEnvironment('A21_BROWSER_PYTHON')
  const root = requiredEnvironment('A21_BROWSER_ROOT')
  const environment: NodeJS.ProcessEnv = { ...process.env, PYTHONNOUSERSITE: '1' }
  for (const name of ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PYTHONUSERBASE']) delete environment[name]
  return execFileSync(python, ['-c', code, join(root, 'published.sqlite3'), ...arguments_], {
    encoding: 'utf8', env: environment,
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

type Evidence = {
  invocationId: string
  endpointId: string
  hits: number
  sensitiveAudits: number
  detailViews: number
  tools: number
  invocations: number
}

function databaseEvidence(invocationId: string): Evidence {
  const code = `
import json,sqlite3,sys
connection=sqlite3.connect(sys.argv[1])
invocation=sys.argv[2]
row=connection.execute("SELECT id,endpoint_id FROM endpoint_invocations WHERE id=?",(invocation,)).fetchall()
if len(row)!=1: raise SystemExit(2)
hits=connection.execute("SELECT count(*) FROM invocation_sensitive_hits WHERE invocation_id=?",(invocation,)).fetchone()[0]
audits=connection.execute("SELECT count(*) FROM audit_events WHERE invocation_id=? AND action='published_api.sensitive_data_detected'",(invocation,)).fetchone()[0]
views=connection.execute("SELECT count(*) FROM audit_events WHERE invocation_id=? AND action='audit.detail.view'",(invocation,)).fetchone()[0]
tools=connection.execute("SELECT count(*) FROM endpoint_tool_calls WHERE invocation_id=?",(invocation,)).fetchone()[0]
invocations=connection.execute("SELECT count(*) FROM endpoint_invocations").fetchone()[0]
connection.close()
print(json.dumps([row[0][0],row[0][1],hits,audits,views,tools,invocations],separators=(',',':')))
`
  const [foundInvocation, endpointId, hits, sensitiveAudits, detailViews, tools, invocations] =
    JSON.parse(databaseCommand(code, invocationId)) as [string, string, number, number, number, number, number]
  return { invocationId: foundInvocation, endpointId, hits, sensitiveAudits, detailViews, tools, invocations }
}

function redactCanonicalInvocation(invocationId: string): void {
  const code = `
import sqlite3,sys,time
from 繁中代理.發布介面.治理.遮蔽 import SQLite不可逆遮蔽服務
database=sys.argv[1]
invocation=sys.argv[2]
with sqlite3.connect(database) as connection:
 tools=connection.execute("SELECT id FROM endpoint_tool_calls WHERE invocation_id=? ORDER BY sequence_number",(invocation,)).fetchall()
if len(tools)!=2: raise SystemExit(2)
service=SQLite不可逆遮蔽服務(database)
targets=(("input","invocation_input",invocation,"/contact"),("metadata","metadata",invocation,"/phone"),("response","output",invocation,"/answer"),("arguments","tool_arguments",tools[0][0],"/category"),("result","tool_result",tools[1][0],"/result/content"))
now=time.time()
for index,(suffix,target,row,path) in enumerate(targets):
 service.遮蔽(True,"redaction-browser-"+suffix,"audit-redaction-browser-"+suffix,"browser-admin-a21","request-redaction-browser-"+suffix,invocation,target,row,path,"privacy",now+index)
`
  databaseCommand(code, invocationId)
}

function observeResponses(page: Page) {
  const consoleMessages: string[] = []
  const pageErrors: string[] = []
  page.on('console', (message) => consoleMessages.push(message.text()))
  page.on('pageerror', (error) => pageErrors.push(error.message))
  return { consoleMessages, pageErrors }
}

async function assertRawAbsent(
  page: Page, observed: ReturnType<typeof observeResponses>, responseBodies: string[],
) {
  await page.waitForLoadState('networkidle')
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
  const inspected = JSON.stringify([state, observed.consoleMessages, observed.pageErrors, responseBodies])
  if (RAW_MARKERS.some((marker) => inspected.includes(marker))) {
    throw new Error('raw browser state escaped safe detail boundary')
  }
}

async function openDetail(page: Page, endpointId: string, invocationId: string): Promise<string> {
  await page.getByRole('button', { name: '完整呼叫紀錄' }).click()
  await page.getByLabel('端點識別碼').fill(endpointId)
  await page.getByRole('button', { name: '查詢', exact: true }).click()
  const invocation = page.getByRole('button', { name: new RegExp(invocationId) })
  await expect(invocation).toBeVisible()
  const detailResponse = page.waitForResponse((response: Response) =>
    new URL(response.url()).pathname === `/api/admin/endpoints/${endpointId}/invocations/${invocationId}` &&
    response.status() === 200)
  await invocation.click()
  await expect(page.getByRole('heading', { name: '呼叫詳情' })).toBeVisible()
  return (await detailResponse).text()
}

async function assertFiveSafeTargets(page: Page) {
  const hits = page.getByLabel('敏感資料命中')
  await expect(hits).toBeVisible()
  for (const label of ['輸入', 'Metadata', '回應資料', '工具參數', '工具結果']) {
    await expect(hits.getByText(label, { exact: true })).toBeVisible()
  }
  for (const value of [
    '/contact', '/phone', '/answer', '/category', '/result/content',
    'email', 'phone', 'payment_card_candidate',
  ]) {
    await expect(hits).toContainText(value)
  }
  await expect(hits).toContainText('無資料')
  await expect(hits).not.toContainText(/raw|snippet|hash|audit[_ -]?id|稽核識別碼/i)
}

test('A21 production SPA canonical Admin sensitive-hit browser closure', async ({ browser, request }) => {
  if (PHASE === 'restart') {
    const invocationId = requiredEnvironment('A21_BROWSER_INVOCATION_ID')
    const evidence = databaseEvidence(invocationId)
    expect(evidence).toMatchObject({ invocationId, hits: 5, sensitiveAudits: 5, tools: 2, invocations: 1 })
    const context = await browser.newContext()
    const page = await context.newPage()
    const observed = observeResponses(page)
    await page.goto('/')
    await login(page, 'browser-admin-a21')
    const detailBody = await openDetail(page, evidence.endpointId, invocationId)
    await assertFiveSafeTargets(page)
    await assertRawAbsent(page, observed, [detailBody])
    expect(observed.pageErrors).toEqual([])
    await context.close()
    return
  }

  const invoke = await request.post(`/v1/endpoints/${SLUG}/invoke`, {
    headers: { Authorization: `Bearer ${API_KEY}` },
    data: {
      input: { contact: RAW_MARKERS[0] },
      metadata: { phone: RAW_MARKERS[1] },
    },
  })
  expect(invoke.status()).toBe(200)
  const invokeBody = await invoke.json() as Record<string, any>
  expect(invokeBody.warnings).toEqual([WARNING])
  expect(Object.keys(invokeBody.warnings[0]).sort()).toEqual(['code', 'message'])
  const invocationId = invokeBody.invocation?.id
  if (typeof invocationId !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(invocationId)) {
    throw new Error('canonical invoke response identity unavailable')
  }
  const beforeRedaction = databaseEvidence(invocationId)
  expect(beforeRedaction).toMatchObject({ invocationId, hits: 5, sensitiveAudits: 5, tools: 2, invocations: 1 })
  redactCanonicalInvocation(invocationId)
  const evidence = databaseEvidence(invocationId)
  expect(evidence.hits).toBe(5)
  expect(evidence.sensitiveAudits).toBe(evidence.hits)

  const detailRoute = `/api/admin/endpoints/${evidence.endpointId}/invocations/${invocationId}`
  const anonymous = await browser.newContext()
  const anonymousPage = await anonymous.newPage()
  const deepLink = await anonymousPage.goto('/admin/invocations')
  expect(deepLink?.status()).toBe(200)
  expect(deepLink?.headers()['cache-control']).toBe('no-store')
  await expect(anonymousPage.getByRole('heading', { name: '登入智慧工作空間' })).toBeVisible()
  expect((await anonymous.request.get(detailRoute)).status()).toBe(401)
  await anonymous.close()

  const member = await browser.newContext()
  const memberPage = await member.newPage()
  const memberAdminRequests: string[] = []
  memberPage.on('request', (memberRequest) => {
    if (memberRequest.url().includes('/api/admin/')) memberAdminRequests.push(memberRequest.url())
  })
  await memberPage.goto('/')
  await login(memberPage, 'stable-owner', '[REDACTED]')
  await expect(memberPage.getByRole('button', { name: '完整呼叫紀錄' })).toHaveCount(0)
  await expect(memberPage.getByLabel('敏感資料命中')).toHaveCount(0)
  expect((await member.request.get(detailRoute)).status()).toBe(403)
  expect(memberAdminRequests).toHaveLength(0)
  await member.close()

  const admin = await browser.newContext()
  const page = await admin.newPage()
  const observed = observeResponses(page)
  await page.goto('/')
  await login(page, 'browser-admin-a21')
  await page.getByRole('button', { name: '完整呼叫紀錄' }).click()
  await page.getByLabel('端點識別碼').fill(evidence.endpointId)
  await page.getByRole('button', { name: '查詢', exact: true }).click()
  const invocation = page.getByRole('button', { name: new RegExp(invocationId) })
  await expect(invocation).toBeVisible()

  const viewsBeforeFailure = databaseEvidence(invocationId).detailViews
  setDetailAuditFailure(true)
  try {
    await invocation.click()
    await expect(page.getByRole('alert')).toHaveText('目前無法載入完整呼叫紀錄，請稍後再試。')
    await expect(page.getByRole('heading', { name: '呼叫詳情' })).toHaveCount(0)
    expect(databaseEvidence(invocationId).detailViews).toBe(viewsBeforeFailure)
  } finally {
    setDetailAuditFailure(false)
  }

  const detailResponse = page.waitForResponse((response: Response) =>
    new URL(response.url()).pathname === detailRoute && response.status() === 200)
  await invocation.click()
  await expect(page.getByRole('heading', { name: '呼叫詳情' })).toBeVisible()
  const detailBody = await (await detailResponse).text()
  await assertFiveSafeTargets(page)
  const warning = page.getByLabel('執行事件')
  await expect(warning).toContainText(WARNING.message)
  await expect(warning).not.toContainText(/payment_card_candidate|email|phone|\/contact|\/phone|\/answer|5 筆/)
  expect((await admin.request.get(`/api/admin/endpoints/${evidence.endpointId}/invocations/missing-a21`)).status())
    .toBe(404)

  const finalEvidence = databaseEvidence(invocationId)
  expect(finalEvidence).toMatchObject({ hits: 5, sensitiveAudits: 5, tools: 2, invocations: 1 })
  expect(finalEvidence.detailViews).toBeGreaterThan(viewsBeforeFailure)
  await assertRawAbsent(page, observed, [detailBody])
  expect(observed.pageErrors).toEqual([])
  await admin.close()
})
