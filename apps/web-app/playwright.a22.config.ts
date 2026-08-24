import { defineConfig } from '@playwright/test'
import { resolve } from 'node:path'

function shellQuote(value: string): string { return `'${value.replaceAll("'", `'"'"'`)}'` }
const python = process.env.A22_BROWSER_PYTHON
const baseURL = process.env.A22_BROWSER_BASE_URL
const outputDir = process.env.A22_BROWSER_ARTIFACT_ROOT
if (!python || !baseURL || !outputDir) throw new Error('A22 browser smoke environment is incomplete')

export default defineConfig({
  testDir: './browser/tests', testMatch: 'a22-owner-admin.spec.ts', outputDir,
  fullyParallel: false, workers: 1, retries: 0, timeout: 60_000,
  expect: { timeout: 8_000 }, reporter: [['line']],
  use: { baseURL, browserName: 'chromium', headless: true, trace: 'off', screenshot: 'off', video: 'off' },
  webServer: {
    command: `${shellQuote(python)} ${shellQuote(resolve('browser/啟動A22Browser伺服器.py'))}`,
    url: `${baseURL}/healthz`, reuseExistingServer: false, timeout: 30_000,
    stdout: 'pipe', stderr: 'pipe',
  },
})
