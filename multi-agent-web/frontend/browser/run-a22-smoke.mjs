import { access, chmod, mkdtemp, readFile, readdir, realpath, rm } from 'node:fs/promises'
import { constants } from 'node:fs'
import { createServer } from 'node:net'
import { createHash, randomBytes } from 'node:crypto'
import { tmpdir } from 'node:os'
import { join, relative, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'

async function availablePort() {
  return await new Promise((resolvePort, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (!address || typeof address === 'string') {
        return server.close(() => reject(new Error('port unavailable')))
      }
      server.close((error) => error ? reject(error) : resolvePort(address.port))
    })
  })
}

function isolated(overrides = {}) {
  const environment = { ...process.env, ...overrides }
  for (const name of ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PYTHONUSERBASE']) delete environment[name]
  environment.PYTHONNOUSERSITE = '1'
  environment.PYTHONDONTWRITEBYTECODE = '1'
  return environment
}

const backendRoot = resolve('../../multi-agent-service/backend')

async function removable(path) {
  const entries = await readdir(path, { withFileTypes: true }).catch((error) => {
    if (error?.code === 'ENOENT') return []
    throw error
  })
  for (const entry of entries) {
    if (entry.isDirectory() && !entry.isSymbolicLink()) await removable(join(path, entry.name))
  }
  await chmod(path, 0o700)
}

async function collectFiles(root) {
  const absoluteRoot = resolve(root)
  const found = []
  async function walk(directory) {
    const entries = await readdir(directory, { withFileTypes: true })
    for (const entry of entries) {
      const path = join(directory, entry.name)
      if (entry.isDirectory() && !entry.isSymbolicLink()) await walk(path)
      else if (entry.isFile()) found.push(relative(process.cwd(), path))
    }
  }
  await walk(absoluteRoot)
  return found.sort()
}

async function fingerprint(paths) {
  const hash = createHash('sha256')
  for (const path of [...new Set(paths)].sort()) {
    hash.update(path)
    hash.update('\0')
    hash.update(await readFile(resolve(path)))
    hash.update('\0')
  }
  return hash.digest('hex')
}

const fixedSourceFiles = [
  'package.json', 'package-lock.json', 'tsconfig.json', 'tsconfig.browser.json', 'vite.config.ts',
  'playwright.a22.config.ts', 'browser/run-a22-smoke.mjs', 'browser/啟動A22Browser伺服器.py',
  'browser/檢查A22Browser資料庫.py', 'browser/tests/a22-owner-admin.spec.ts',
  'browser/tests/a19-owner-observability.spec.ts', 'browser/tests/a21-admin-sensitive-hits.spec.ts',
  join(backendRoot, 'tests/發布介面/test_前端管理整合.py'),
  join(backendRoot, 'tests/e2e/test_owner_admin_frontend.py'),
]

async function sourceSnapshot() {
  const paths = [...fixedSourceFiles, ...await collectFiles('src')]
  return { paths, hash: await fingerprint(paths) }
}

const python = process.env.A22_BROWSER_PYTHON
const plannerProject = 'lab-cola-rd'
const skipPlanner = process.env.A22_SKIP_PLANNER === '1'
if (!python || !python.startsWith('/')) throw new Error('A22_BROWSER_PYTHON absolute path is required')
await access(python, constants.X_OK)
const preflight = spawnSync(python, ['-c', 'import fastapi,uvicorn,pydantic_core,google.auth,google.auth.transport.requests'], {
  cwd: backendRoot, encoding: 'utf8', env: isolated(),
})
if (preflight.status !== 0) throw new Error('A22 browser Python dependency preflight failed')
const lock = createHash('sha256').update(await readFile(resolve('package-lock.json'))).digest('hex')
if (lock !== '0ea2528135fc4b610ce951c5c9d9ecf5cc9b889c11dfd43e5c24b86c2d2d07e9') {
  throw new Error('A22 package lock authority changed')
}

const sourceBeforeBuild = await sourceSnapshot()
const build = spawnSync('npm', ['run', 'build'], {
  cwd: process.cwd(), stdio: 'inherit', env: process.env, timeout: 120_000,
})
if (build.status !== 0) throw new Error('A22 production build failed')
const sourceBefore = await sourceSnapshot()
if (sourceBefore.hash !== sourceBeforeBuild.hash || sourceBefore.paths.join('\0') !== sourceBeforeBuild.paths.join('\0')) {
  throw new Error('A22 source changed during build')
}
const distFiles = await collectFiles('dist')
if (distFiles.length === 0) throw new Error('A22 production dist unavailable')
const distBefore = await fingerprint(distFiles)

const alias = await mkdtemp(join(tmpdir(), 'testagent2-a22-browser-'))
let status = 1
try {
  const temporary = await realpath(alias)
  const root = join(temporary, 'state')
  const password = randomBytes(32).toString('base64url')
  const credentialKey = randomBytes(32).toString('base64url')
  const ownerKey = randomBytes(32).toString('base64url')
  const rawMarker = `canary-${randomBytes(13).toString('hex')}`
  const pids = []

  for (const phase of ['primary', 'restart']) {
    const port = await availablePort()
    const environment = isolated({
      A22_BROWSER_PYTHON: python,
      A22_BROWSER_ROOT: root,
      A22_BROWSER_ARTIFACT_ROOT: join(temporary, `playwright-results-${phase}`),
      A22_BROWSER_DIST_ROOT: resolve('dist'),
      A22_BROWSER_PORT: String(port),
      A22_BROWSER_PASSWORD: password,
      A22_BROWSER_CREDENTIAL_KEY: credentialKey,
      A22_BROWSER_OWNER_CURSOR_KEY: ownerKey,
      A22_BROWSER_RAW_MARKER: rawMarker,
      A22_BROWSER_BASE_URL: `http://127.0.0.1:${port}`,
      A22_BROWSER_PHASE: phase,
      A22_BROWSER_PID_FILE: join(temporary, `${phase}.pid`),
    })
    const browser = spawnSync(resolve('node_modules/.bin/playwright'), [
      'test', '--config=playwright.a22.config.ts',
    ], { cwd: process.cwd(), stdio: 'inherit', env: environment, timeout: 120_000 })
    status = browser.status ?? 1
    if (status !== 0) break

    const pid = (await readFile(join(temporary, `${phase}.pid`), 'utf8')).trim()
    if (!/^[1-9][0-9]*$/.test(pid) || pids.includes(pid)) {
      status = 1
      break
    }
    pids.push(pid)
    const checker = spawnSync(python, [
      resolve('browser/檢查A22Browser資料庫.py'), join(root, 'published.sqlite3'), phase,
    ], { encoding: 'utf8', env: isolated(), timeout: 20_000 })
    if (checker.status !== 0 || !checker.stdout.includes('A22_DB_CHECK=PASS ')) {
      console.log(`A22_DB_CHECK=FAIL phase=${phase} exit=${checker.status ?? 1} ${checker.stdout.trim()}`)
      status = 1
      break
    }
    console.log(`${phase.toUpperCase()} ${checker.stdout.trim()}`)
  }

  if (status === 0 && pids.length === 2) {
    console.log('A22_NON_PLANNER=PASS')
    console.log('A22_SERVER_PIDS_DISTINCT=PASS phases=2')
  } else {
    status = 1
  }

  if (status === 0 && skipPlanner) {
    console.log('PLANNER_LIVE=BLOCKED reason=LAB_ACCESS_UNAVAILABLE')
    status = 2
  } else if (status === 0) {
    const adc = spawnSync(python, ['-c', [
      'import google.auth',
      'from google.auth.transport.requests import Request',
      'credentials,_=google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])',
      'credentials.refresh(Request())',
      'assert credentials.valid',
      'print("READY")',
    ].join(';')], { encoding: 'utf8', env: isolated(), timeout: 30_000 })
    if (adc.status !== 0) {
      console.log('PLANNER_LIVE=BLOCKED reason=ADC_REFRESH_UNAVAILABLE')
      status = 2
    } else {
      const plannerRoot = join(temporary, 'planner-state')
      const port = await availablePort()
      const environment = isolated({
        A22_BROWSER_PYTHON: python,
        A22_BROWSER_ROOT: plannerRoot,
        A22_BROWSER_ARTIFACT_ROOT: join(temporary, 'playwright-results-planner'),
        A22_BROWSER_DIST_ROOT: resolve('dist'),
        A22_BROWSER_PORT: String(port),
        A22_BROWSER_PASSWORD: password,
        A22_BROWSER_CREDENTIAL_KEY: credentialKey,
        A22_BROWSER_OWNER_CURSOR_KEY: ownerKey,
        A22_BROWSER_RAW_MARKER: rawMarker,
        A22_BROWSER_GCP_PROJECT: plannerProject,
        A22_BROWSER_BASE_URL: `http://127.0.0.1:${port}`,
        A22_BROWSER_PHASE: 'planner',
        A22_BROWSER_PID_FILE: join(temporary, 'planner.pid'),
      })
      const planner = spawnSync(resolve('node_modules/.bin/playwright'), [
        'test', '--config=playwright.a22.config.ts',
      ], { cwd: process.cwd(), stdio: 'inherit', env: environment, timeout: 180_000 })
      status = planner.status ?? 1
      console.log(status === 0
        ? 'PLANNER_LIVE=PASS model=gemini-2.5-flash-lite flow=DRAFT_PUBLISH'
        : 'PLANNER_LIVE=FAIL model=gemini-2.5-flash-lite flow=DRAFT_PUBLISH')
    }
  }

  const sourceAfter = await sourceSnapshot()
  const distAfterFiles = await collectFiles('dist')
  const distAfter = await fingerprint(distAfterFiles)
  if (sourceAfter.hash !== sourceBefore.hash || sourceAfter.paths.join('\0') !== sourceBefore.paths.join('\0') ||
      distAfterFiles.join('\0') !== distFiles.join('\0') || distAfter !== distBefore) {
    status = 1
  }
  if (status === 0 || status === 2) {
    console.log(`A22_SOURCE_SHA256=${sourceAfter.hash}`)
    console.log(`A22_DIST_SHA256=${distAfter}`)
  }
} finally {
  await removable(alias).catch(() => undefined)
  await rm(alias, { recursive: true, force: true })
}
process.exitCode = status
