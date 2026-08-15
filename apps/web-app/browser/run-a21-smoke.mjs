import { access, chmod, mkdtemp, readdir, realpath, rm } from 'node:fs/promises'
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

async function makeTreeRemovable(path) {
  const entries = await readdir(path, { withFileTypes: true }).catch((error) => {
    if (error?.code === 'ENOENT') return []
    throw error
  })
  for (const entry of entries) {
    if (entry.isDirectory() && !entry.isSymbolicLink()) {
      await makeTreeRemovable(join(path, entry.name))
    }
  }
  await chmod(path, 0o700)
}

const python = process.env.A21_BROWSER_PYTHON
if (!python || !python.startsWith('/')) throw new Error('A21_BROWSER_PYTHON absolute path is required')
await access(python, constants.X_OK)
const preflight = spawnSync(python, ['-c', 'import fastapi, uvicorn, pydantic_core'], {
  encoding: 'utf8', env: isolatedPythonEnvironment({ AIAGENT_MODEL_MODE: 'fake' }),
})
if (preflight.status !== 0) throw new Error('A21 browser Python dependency preflight failed')

const temporaryAlias = await mkdtemp(join(tmpdir(), 'testagent2-a21-browser-'))
const temporary = await realpath(temporaryAlias)
let status = 1
let invocationId = ''
try {
  const stateRoot = join(temporary, 'state')
  const password = randomBytes(32).toString('base64url')
  const credentialKey = randomBytes(32).toString('base64url')
  const ownerKey = randomBytes(32).toString('base64url')
  const apiKey = `pk_${randomBytes(32).toString('base64url')}`
  for (const phase of ['primary', 'restart']) {
    const port = await availablePort()
    const result = spawnSync(resolve('node_modules/.bin/playwright'), ['test', '--config=playwright.a21.config.ts'], {
      cwd: process.cwd(), stdio: 'inherit',
      env: isolatedPythonEnvironment({
        AIAGENT_MODEL_MODE: 'fake',
        A21_BROWSER_PYTHON: python,
        A21_BROWSER_ROOT: stateRoot,
        A21_BROWSER_ARTIFACT_ROOT: join(temporary, `playwright-results-${phase}`),
        A21_BROWSER_DIST_ROOT: resolve('dist'),
        A21_BROWSER_PORT: String(port),
        A21_BROWSER_PASSWORD: password,
        A21_BROWSER_API_KEY: apiKey,
        A21_BROWSER_CREDENTIAL_KEY: credentialKey,
        A21_BROWSER_OWNER_CURSOR_KEY: ownerKey,
        A21_BROWSER_BASE_URL: `http://127.0.0.1:${port}`,
        A21_BROWSER_PHASE: phase,
        ...(phase === 'restart' ? { A21_BROWSER_INVOCATION_ID: invocationId } : {}),
      }),
    })
    status = result.status ?? 1
    if (status !== 0) break
    if (phase === 'primary') {
      const evidence = spawnSync(python, ['-c', [
        'import sqlite3,sys',
        'connection=sqlite3.connect(sys.argv[1])',
        'rows=connection.execute("SELECT id FROM endpoint_invocations").fetchall()',
        'connection.close()',
        'assert len(rows)==1',
        'print(rows[0][0])',
      ].join(';'), join(stateRoot, 'published.sqlite3')], {
        encoding: 'utf8', env: isolatedPythonEnvironment(),
      })
      invocationId = evidence.stdout.trim()
      if (evidence.status !== 0 || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(invocationId)) {
        status = 1
        break
      }
    }
  }
} finally {
  await makeTreeRemovable(temporaryAlias)
  await rm(temporaryAlias, { recursive: true, force: true })
}
process.exitCode = status
