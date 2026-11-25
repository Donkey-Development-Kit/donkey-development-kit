// Link and anchor check over the static export (`out/`), no dependencies.
//
// Every internal <a href>, <link href>, <script src> and <img src> in every
// built HTML page must resolve to a file in out/, and every `#fragment` must
// match an id (or <a name>) in the target page. External (http/https/mailto/...)
// links are not fetched: that would make CI depend on the network.
//
// Run: `npm run build && npm run check:links` (build with the same
// DOCS_BASE_PATH to check a sub-path export; it is read from the env here).
// Exit code 1 lists every broken link with its source page.

import { readdir, readFile, stat } from 'node:fs/promises'
import { dirname, join, posix, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { MOVED_ANCHORS, PAGE_REDIRECTS, idFromHash, normalisePath } from '../lib/redirects.mjs'

const OUT = resolve(process.argv[2] ?? join(dirname(fileURLToPath(import.meta.url)), '..', 'out'))
const BASE = (process.env.DOCS_BASE_PATH ?? '').replace(/\/+$/, '')

async function* walk(dir) {
  for (const e of await readdir(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) yield* walk(p)
    else yield p
  }
}

async function isFile(p) {
  try {
    return (await stat(p)).isFile()
  } catch {
    return false
  }
}

const decode = (s) => {
  try {
    return decodeURIComponent(s)
  } catch {
    return s
  }
}

// Map a URL path (already stripped of BASE, query and hash) to a file in out/.
async function resolveFile(urlPath) {
  const rel = decode(urlPath).replace(/^\/+/, '')
  const candidates = urlPath.endsWith('/') || rel === ''
    ? [join(OUT, rel, 'index.html')]
    : [join(OUT, rel), join(OUT, `${rel}.html`), join(OUT, rel, 'index.html')]
  for (const c of candidates) if (await isFile(c)) return c
  return null
}

const ATTR = /<(a|link|script|img|source|iframe)\b[^>]*?\s(href|src)\s*=\s*("([^"]*)"|'([^']*)')/gi
// `id=` on any element; `name=` only on <a> (not <meta name>, form fields).
const IDS = /\sid\s*=\s*(?:"([^"]*)"|'([^']*)')/gi
const A_NAMES = /<a\b[^>]*?\sname\s*=\s*(?:"([^"]*)"|'([^']*)')/gi

const idCache = new Map()
async function idsOf(file) {
  if (!idCache.has(file)) {
    const html = await readFile(file, 'utf8')
    const found = [...html.matchAll(IDS), ...html.matchAll(A_NAMES)]
    idCache.set(file, new Set(found.map((m) => decode(m[1] ?? m[2]))))
  }
  return idCache.get(file)
}

const pages = []
for await (const f of walk(OUT)) if (f.endsWith('.html')) pages.push(f)
if (pages.length === 0) {
  console.error(`check-links: no HTML pages under ${OUT}; run \`npm run build\` first.`)
  process.exit(1)
}

// Nextra's skip-nav link on the error pages points at a #nextra-skip-nav target
// that only content pages render. It is a theme quirk, not a content link.
const ERROR_PAGES = new Set(['/404.html', '/_not-found.html'])

const broken = []
let checked = 0
const STUB_MARK = 'name="ddk-redirect-stub"'
for (const page of pages) {
  const html = await readFile(page, 'utf8')
  // Redirect stubs (scripts/write-redirects.mjs) are for legacy traffic; the
  // rule that content must not link to a moved route does not apply to them.
  const isStub = html.includes(STUB_MARK)
  const pageUrl = '/' + relative(OUT, page).split(/[\\/]/).join('/')
  const pageDir = posix.dirname(pageUrl)
  for (const m of html.matchAll(ATTR)) {
    const raw = (m[4] ?? m[5] ?? '').trim()
    if (!raw || /^[a-z][a-z0-9+.-]*:/i.test(raw) || raw.startsWith('//')) continue // external / mailto / data
    if (m[1].toLowerCase() === 'link' && /rel\s*=\s*["']?(preconnect|dns-prefetch)/i.test(m[0])) continue
    checked++
    const [beforeHash, ...hashParts] = raw.split('#')
    const hash = hashParts.join('#')
    const pathPart = beforeHash.split('?')[0]
    let target
    if (pathPart === '') target = page
    else {
      let abs = pathPart.startsWith('/') ? pathPart : posix.resolve(pageDir.endsWith('/') ? pageDir : pageDir + '/', pathPart)
      if (BASE && (abs === BASE || abs.startsWith(BASE + '/'))) abs = abs.slice(BASE.length) || '/'
      else if (BASE && pathPart.startsWith('/')) {
        broken.push(`${pageUrl}: ${raw} (missing the ${BASE} base path)`)
        continue
      }
      if (!isStub) {
        const route = normalisePath(abs)
        if (PAGE_REDIRECTS[route]) {
          broken.push(`${pageUrl}: ${raw} (moved page: link to ${PAGE_REDIRECTS[route]} instead)`)
          continue
        }
        const movedTo = hash ? MOVED_ANCHORS[route]?.[idFromHash(hash)] : undefined
        if (movedTo) {
          broken.push(`${pageUrl}: ${raw} (section moved: link to ${movedTo}#${hash} instead)`)
          continue
        }
      }
      target = await resolveFile(abs)
      if (!target) {
        broken.push(`${pageUrl}: ${raw} (no such file in out/)`)
        continue
      }
    }
    if (hash && !(ERROR_PAGES.has(pageUrl) && hash === 'nextra-skip-nav') && target.endsWith('.html') && decode(hash) !== 'top') {
      if (!(await idsOf(target)).has(decode(hash))) broken.push(`${pageUrl}: ${raw} (no #${hash} anchor in target)`)
    }
  }
}

// The redirect registry (lib/redirects.mjs) must agree with the export: every
// page redirect has a stub and a real target; every moved anchor has a real
// target page that carries the id.
for (const [from, to] of Object.entries(PAGE_REDIRECTS)) {
  if (!(await resolveFile(to))) broken.push(`redirect ${from} -> ${to} (target is not exported)`)
  const stub = join(OUT, `${from.replace(/^\/+/, '')}.html`)
  if (!(await isFile(stub))) broken.push(`redirect ${from} (no stub written at ${relative(OUT, stub)})`)
}
for (const [from, anchors] of Object.entries(MOVED_ANCHORS)) {
  if (!(await resolveFile(from))) broken.push(`moved anchors ${from} (source page is not exported)`)
  for (const [id, to] of Object.entries(anchors)) {
    const file = await resolveFile(to)
    if (!file) broken.push(`moved anchor ${from}#${id} -> ${to} (target is not exported)`)
    else if (!(await idsOf(file)).has(id)) broken.push(`moved anchor ${from}#${id} -> ${to} (no #${id} in target)`)
  }
}

if (broken.length) {
  console.error(`check-links: ${broken.length} broken link(s) in ${pages.length} pages:`)
  for (const b of [...new Set(broken)].sort()) console.error(`  ${b}`)
  process.exit(1)
}
console.log(`check-links: ${checked} internal links across ${pages.length} pages OK.`)
