import { access, chmod, mkdtemp, readFile, readdir, realpath, rm } from 'node:fs/promises'
import { constants } from 'node:fs'
import { createServer } from 'node:net'
import { createHash, randomBytes } from 'node:crypto'
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

function isolatedEnvironment(overrides = {}) {
  const environment = { ...process.env, ...overrides }
  for (const name of ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PYTHONUSERBASE']) delete environment[name]
  environment.PYTHONNOUSERSITE = '1'
  environment.PYTHONDONTWRITEBYTECODE = '1'
  return environment
}

const backendRoot = resolve('../../multi-agent-service/backend')

const candidateFiles = [
  'package.json', 'tsconfig.browser.json', 'playwright.a20.config.ts',
  'browser/run-a20-smoke.mjs', 'browser/啟動A20Browser伺服器.py',
  'browser/檢查A20Browser資料庫.py', 'browser/tests/a20-redaction-tombstone.spec.ts',
  'src/api/logs.ts', 'src/tests/invocation-logs.test.tsx',
  join(backendRoot, '繁中代理/發布介面/治理/管理查詢契約.py'),
  join(backendRoot, 'tests/發布介面/test_管理查詢.py'),
]

async function candidateFingerprint() {
  const hash = createHash('sha256')
  for (const path of candidateFiles) {
    hash.update(path)
    hash.update('\0')
    hash.update(await readFile(resolve(path)))
    hash.update('\0')
  }
  return hash.digest('hex')
}

async function makeTreeRemovable(path) {
  const entries = await readdir(path, { withFileTypes: true }).catch((error) => {
    if (error?.code === 'ENOENT') return []
    throw error
  })
  for (const entry of entries) {
    if (entry.isDirectory() && !entry.isSymbolicLink()) await makeTreeRemovable(join(path, entry.name))
  }
  await chmod(path, 0o700)
}

const python = process.env.A20_BROWSER_PYTHON
if (!python || !python.startsWith('/')) throw new Error('A20_BROWSER_PYTHON absolute path is required')
await access(python, constants.X_OK)
const preflight = spawnSync(python, ['-c', 'import fastapi,uvicorn,pydantic_core,繁中代理'], {
  cwd: backendRoot, encoding: 'utf8', env: isolatedEnvironment({ AIAGENT_MODEL_MODE: 'fake' }),
})
if (preflight.status !== 0) throw new Error('A20 browser Python dependency preflight failed')

const temporaryAlias = await mkdtemp(join(tmpdir(), 'testagent2-a20-browser-'))
const temporary = await realpath(temporaryAlias)
const stateRoot = join(temporary, 'state')
const password = randomBytes(32).toString('base64url')
const sourceBefore = await candidateFingerprint()
const serverPids = []
let status = 1
try {
  for (const phase of ['primary', 'restart']) {
    const port = await availablePort()
    const environment = isolatedEnvironment({
      AIAGENT_MODEL_MODE: 'fake', A20_BROWSER_PYTHON: python,
      A20_BROWSER_ROOT: stateRoot,
      A20_BROWSER_ARTIFACT_ROOT: join(temporary, `playwright-results-${phase}`),
      A20_BROWSER_DIST_ROOT: resolve('dist'), A20_BROWSER_PORT: String(port),
      A20_BROWSER_PASSWORD: password, A20_BROWSER_BASE_URL: `http://127.0.0.1:${port}`,
      A20_BROWSER_PHASE: phase,
      A20_BROWSER_PID_FILE: join(temporary, `${phase}.pid`),
    })
    const browser = spawnSync(resolve('node_modules/.bin/playwright'), [
      'test', '--config=playwright.a20.config.ts',
    ], { cwd: process.cwd(), stdio: 'inherit', env: environment, timeout: 90_000 })
    status = browser.status ?? 1
    if (status !== 0) break
    const pidText = (await readFile(join(temporary, `${phase}.pid`), 'utf8')).trim()
    if (!/^[1-9][0-9]*$/.test(pidText) || serverPids.includes(pidText)) {
      status = 1
      break
    }
    serverPids.push(pidText)
    const checker = spawnSync(python, [
      resolve('browser/檢查A20Browser資料庫.py'), join(stateRoot, 'published.sqlite3'),
    ], { cwd: process.cwd(), encoding: 'utf8', env: isolatedEnvironment(), timeout: 20_000 })
    if (checker.status !== 0 || checker.stdout.trim() !== 'A20_DB_CHECK=PASS redactions=3 tombstones=3') {
      status = 1
      break
    }
    console.log(`${phase.toUpperCase()} ${checker.stdout.trim()}`)
  }
  const sourceAfter = await candidateFingerprint()
  if (sourceAfter !== sourceBefore || serverPids.length !== 2) status = 1
  if (status === 0) {
    console.log(`A20_SOURCE_SHA256=${sourceAfter}`)
    console.log(`A20_SERVER_PIDS_DISTINCT=PASS phases=${serverPids.length}`)
  }
} finally {
  await makeTreeRemovable(temporaryAlias)
  await rm(temporaryAlias, { recursive: true, force: true })
}
process.exitCode = status
