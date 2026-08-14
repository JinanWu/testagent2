import { access, mkdtemp, rm } from 'node:fs/promises'
import { constants } from 'node:fs'
import { createServer } from 'node:net'
import { randomBytes } from 'node:crypto'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'

async function availablePort() {
  return await new Promise((resolvePort, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (address === null || typeof address === 'string') {
        server.close()
        reject(new Error('browser smoke port unavailable'))
        return
      }
      const { port } = address
      server.close((error) => error ? reject(error) : resolvePort(port))
    })
  })
}

const python = process.env.A18_BROWSER_PYTHON
if (!python || !python.startsWith('/')) {
  throw new Error('A18_BROWSER_PYTHON absolute path is required')
}
await access(python, constants.X_OK)
const preflight = spawnSync(python, ['-c', 'import fastapi, uvicorn, pydantic_core'], {
  encoding: 'utf8',
  env: { ...process.env, PYTHONPATH: '', AIAGENT_MODEL_MODE: 'fake' },
})
if (preflight.status !== 0) {
  throw new Error('A18 browser Python dependency preflight failed')
}

const temporaryParent = await mkdtemp(join(tmpdir(), 'testagent2-a18-browser-'))
const stateRoot = join(temporaryParent, 'state')
const artifactRoot = join(temporaryParent, 'playwright-results')
const distRoot = resolve('dist')
const playwright = resolve('node_modules/.bin/playwright')
const port = await availablePort()
const browserPassword = randomBytes(32).toString('base64url')
let status = 1
try {
  const result = spawnSync(playwright, ['test', '--config=playwright.config.ts'], {
    cwd: process.cwd(),
    stdio: 'inherit',
    env: {
      ...process.env,
      PYTHONPATH: '',
      AIAGENT_MODEL_MODE: 'fake',
      A18_BROWSER_PYTHON: python,
      A18_BROWSER_ROOT: stateRoot,
      A18_BROWSER_ARTIFACT_ROOT: artifactRoot,
      A18_BROWSER_DIST_ROOT: distRoot,
      A18_BROWSER_PORT: String(port),
      A18_BROWSER_PASSWORD: browserPassword,
      A18_BROWSER_BASE_URL: `http://127.0.0.1:${port}`,
    },
  })
  status = result.status ?? 1
} finally {
  await rm(temporaryParent, { recursive: true, force: true })
}
process.exitCode = status
