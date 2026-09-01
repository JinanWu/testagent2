import { access, mkdtemp, realpath, rm } from 'node:fs/promises'
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
      if (address === null || typeof address === 'string') return server.close(() => reject(new Error('port unavailable')))
      server.close((error) => error ? reject(error) : resolvePort(address.port))
    })
  })
}

function isolatedPythonEnvironment(overrides = {}) {
  const environment = { ...process.env, ...overrides }
  for (const name of ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PYTHONUSERBASE']) delete environment[name]
  environment.PYTHONNOUSERSITE = '1'
  return environment
}

const python = process.env.A19_BROWSER_PYTHON
if (!python || !python.startsWith('/')) throw new Error('A19_BROWSER_PYTHON absolute path is required')
await access(python, constants.X_OK)
const preflight = spawnSync(python, ['-c', 'import fastapi, uvicorn, pydantic_core'], {
  encoding: 'utf8', env: isolatedPythonEnvironment({ AIAGENT_MODEL_MODE: 'fake' }),
})
if (preflight.status !== 0) throw new Error('A19 browser Python dependency preflight failed')

const temporaryAlias = await mkdtemp(join(tmpdir(), 'testagent2-a19-browser-'))
const temporary = await realpath(temporaryAlias)
let status = 1
try {
  const port = await availablePort()
  const stateRoot = join(temporary, 'state')
  const artifactRoot = join(temporary, 'playwright-results')
  const result = spawnSync(resolve('node_modules/.bin/playwright'), ['test', '--config=playwright.a19.config.ts'], {
    cwd: process.cwd(), stdio: 'inherit',
    env: isolatedPythonEnvironment({
      AIAGENT_MODEL_MODE: 'fake',
      A19_BROWSER_PYTHON: python,
      A19_BROWSER_ROOT: stateRoot,
      A19_BROWSER_ARTIFACT_ROOT: artifactRoot,
      A19_BROWSER_DIST_ROOT: resolve('dist'),
      A19_BROWSER_PORT: String(port),
      A19_BROWSER_PASSWORD: randomBytes(32).toString('base64url'),
      A19_BROWSER_CREDENTIAL_KEY: randomBytes(32).toString('base64url'),
      A19_BROWSER_OWNER_CURSOR_KEY: randomBytes(32).toString('base64url'),
      A19_BROWSER_BASE_URL: `http://127.0.0.1:${port}`,
    }),
  })
  status = result.status ?? 1
} finally {
  await rm(temporaryAlias, { recursive: true, force: true })
}
process.exitCode = status
