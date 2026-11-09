import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdir, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises'
import { createServer, request } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const script = fileURLToPath(new URL('../scripts/preview-pages.mjs', import.meta.url))

async function preview(t, basePath = '') {
  const directory = await mkdtemp(join(tmpdir(), 'ddk-preview-'))
  await mkdir(join(directory, 'out'))
  await writeFile(join(directory, 'out', 'index.html'), 'home')
  await writeFile(join(directory, 'out', 'page.html'), 'page')
  await mkdir(join(directory, 'out', 'nested'))
  await writeFile(join(directory, 'out', 'nested', 'index.html'), 'nested')
  await writeFile(join(directory, 'private.html'), 'outside-secret')
  const reservation = createServer()
  reservation.listen(0, '127.0.0.1')
  await once(reservation, 'listening')
  const port = reservation.address().port
  await new Promise(resolve => reservation.close(resolve))
  const child = spawn(process.execPath, [script], {
    cwd: directory,
    env: { ...process.env, PORT: String(port), DOCS_BASE_PATH: basePath },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill()
      await once(child, 'exit')
    }
    await rm(directory, { recursive: true, force: true })
  })
  await once(child.stdout, 'data')
  return { directory, port }
}

function get(port, path, host = '127.0.0.1') {
  return new Promise((resolve, reject) => {
    const req = request({ hostname: '127.0.0.1', port, path, headers: { host } }, response => {
      let body = ''
      response.setEncoding('utf8')
      response.on('data', chunk => { body += chunk })
      response.on('end', () => resolve({ status: response.statusCode, body, headers: response.headers }))
    })
    req.on('error', reject)
    req.end()
  })
}

test('preview serves exports and retains base-path behavior', async t => {
  const { port } = await preview(t, '/docs')
  assert.equal((await get(port, '/')).headers.location, '/docs/')
  for (const [path, body] of [['/docs/', 'home'], ['/docs/page', 'page'], ['/docs/nested/', 'nested']]) {
    const result = await get(port, path)
    assert.equal(result.status, 200)
    assert.equal(result.body, body)
  }
  assert.equal((await get(port, '/page')).status, 404)
})

test('preview ignores untrusted Host when parsing the request', async t => {
  const { port } = await preview(t)
  assert.equal((await get(port, '/page', '[invalid')).status, 200)
})

test('preview binds explicitly to IPv4 loopback, not all interfaces', async t => {
  const { port } = await preview(t)
  await assert.rejects(new Promise((resolve, reject) => {
    const req = request({ hostname: '::1', port, path: '/' }, resolve)
    req.on('error', reject)
    req.end()
  }), { code: 'ECONNREFUSED' })
  assert.equal((await get(port, '/')).status, 200)
})

test('preview rejects malformed URLs and escapes without crashing', async t => {
  const { port } = await preview(t)
  for (const path of ['/bad%ZZ', '/bad%E0%A4', 'http://[invalid']) {
    assert.equal((await get(port, path)).status, 400)
    assert.equal((await get(port, '/page')).status, 200)
  }
})

test('preview rejects escaping symlinks for every file candidate', async t => {
  const { directory, port } = await preview(t)
  const root = join(directory, 'out')
  await symlink('../private.html', join(root, 'direct.html'))
  await symlink('../private.html', join(root, 'extension.html'))
  await rm(join(root, 'nested', 'index.html'))
  await symlink('../../private.html', join(root, 'nested', 'index.html'))
  await symlink('..', join(root, 'escape'))
  await symlink('../absent.html', join(root, 'dangling.html'))
  await symlink('page.html', join(root, 'inside.html'))
  for (const path of ['/direct.html', '/extension', '/nested/', '/escape/private.html', '/%2e%2e%2fprivate.html', '/dangling.html']) {
    const result = await get(port, path)
    assert.equal(result.status, 404, path)
    assert.ok(!result.body.includes('outside-secret'))
  }
  assert.equal((await get(port, '/inside.html')).body, 'page')
})
