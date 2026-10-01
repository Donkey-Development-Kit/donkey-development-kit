import { createReadStream, existsSync, statSync } from 'node:fs'
import { createServer } from 'node:http'
import { extname, join, normalize, resolve, sep } from 'node:path'

const root = resolve('out')
// Mirrors next.config.mjs: empty (root) unless the export was built with DOCS_BASE_PATH.
const basePath = (process.env.DOCS_BASE_PATH ?? '').replace(/\/+$/, '')
const port = Number(process.env.PORT ?? 4173)

const contentTypes = {
  '.css': 'text/css; charset=utf-8',
  '.gif': 'image/gif',
  '.html': 'text/html; charset=utf-8',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
}

function resolveFile(pathname) {
  const withoutBase = pathname === basePath ? '/' : pathname.slice(basePath.length)
  const relativePath = normalize(decodeURIComponent(withoutBase)).replace(/^[/\\]+/, '')
  const requested = resolve(root, relativePath)

  if (requested !== root && !requested.startsWith(`${root}${sep}`)) return null
  if (!extname(requested) && existsSync(`${requested}.html`)) return `${requested}.html`
  if (existsSync(requested) && statSync(requested).isDirectory()) {
    const index = join(requested, 'index.html')
    return existsSync(index) ? index : null
  }
  if (existsSync(requested)) return requested
  return null
}

createServer((request, response) => {
  const pathname = new URL(request.url ?? '/', `http://${request.headers.host}`).pathname
  if (basePath && pathname === '/') {
    response.writeHead(302, { location: `${basePath}/` })
    response.end()
    return
  }
  if (basePath && pathname !== basePath && !pathname.startsWith(`${basePath}/`)) {
    response.writeHead(404)
    response.end('Not found')
    return
  }

  const file = resolveFile(pathname)
  if (!file) {
    response.writeHead(404)
    response.end('Not found')
    return
  }

  const stream = createReadStream(file)
  stream.on('error', () => {
    if (!response.headersSent) response.writeHead(500)
    response.end('Unable to read generated file')
  })
  stream.on('open', () => {
    response.writeHead(200, {
      'content-type': contentTypes[extname(file)] ?? 'application/octet-stream',
    })
    stream.pipe(response)
  })
}).listen(port, () => {
  console.log(`GitHub Pages preview: http://localhost:${port}${basePath}/`)
})
