import { defineConfig } from '@playwright/test'
import { resolve } from 'node:path'

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`
}

const python = process.env.A21_BROWSER_PYTHON
const baseURL = process.env.A21_BROWSER_BASE_URL
const outputDir = process.env.A21_BROWSER_ARTIFACT_ROOT
if (!python || !baseURL || !outputDir) throw new Error('A21 browser smoke environment is incomplete')

export default defineConfig({
  testDir: './browser/tests',
  testMatch: 'a21-admin-sensitive-hits.spec.ts',
  outputDir,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  reporter: [['line']],
  use: { baseURL, browserName: 'chromium', headless: true, trace: 'off', screenshot: 'off', video: 'off' },
  webServer: {
    command: `${shellQuote(python)} ${shellQuote(resolve('browser/啟動A21Browser伺服器.py'))}`,
    url: `${baseURL}/healthz`,
    reuseExistingServer: false,
    timeout: 30_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
})
