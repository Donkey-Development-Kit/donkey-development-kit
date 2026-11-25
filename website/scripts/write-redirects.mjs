// Writes static redirect stubs for moved pages into out/ (epic #1824, #1825).
//
// The site is a static export: there are no server redirects. For each
// PAGE_REDIRECTS entry this writes, next to Next's own export naming:
//   out/<old>.html  — forwards to the new page, keeping the #fragment, and
//                     honours MOVED_ANCHORS for the same page. Carries a
//                     noindex, a canonical link and a visible fallback link,
//                     and is marked with <meta name="ddk-redirect-stub"> so
//                     check-links can tell it apart from real content.
//   out/<old>.md    — a one-line pointer for markdown/LLM consumers.
//   out/<old>.txt   — the same pointer as plain text.
//
// Stubs are written to out/ only, never public/, so they stay out of
// llms.txt, llms-full.txt and the per-page .md set (public/ is the source).
// Runs as `postbuild` after `next build` and before pagefind, so stubs are
// never indexed. It fails the build on a collision with a real page or a
// missing target.
//
// Run: `npm run build` (postbuild), or `node scripts/write-redirects.mjs`
// after a build. DOCS_BASE_PATH is honoured like next.config.mjs does.

import { access, mkdir, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { MOVED_ANCHORS, PAGE_REDIRECTS, registryProblems } from '../lib/redirects.mjs'
import { SITE_ORIGIN } from '../lib/site.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(process.argv[2] ?? join(HERE, '..', 'out'))
const BASE = (process.env.DOCS_BASE_PATH ?? '').replace(/\/+$/, '')

async function exists(p) {
  try {
    await access(p)
    return true
  } catch {
    return false
  }
}

// A route is exported as <route>.html, or <route>/index.html for a folder index.
async function routeExists(route) {
  const rel = route.replace(/^\/+/, '')
  return (
    (await exists(join(OUT, `${rel}.html`))) || (await exists(join(OUT, rel, 'index.html')))
  )
}

// JSON inlined into a <script>: escape the characters that could close it.
const scriptJson = (value) =>
  JSON.stringify(value).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026')

const escapeHtml = (s) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

export function stubHtml(from, to, moved = MOVED_ANCHORS, base = BASE) {
  const target = `${base}${to}`
  const anchors = moved[from] ?? {}
  const canonical = `${SITE_ORIGIN}${to}`
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="ddk-redirect-stub" content="1">
<meta name="robots" content="noindex">
<title>Moved — Donkey Development Kit</title>
<link rel="canonical" href="${escapeHtml(canonical)}">
<meta http-equiv="refresh" content="0; url=${escapeHtml(target)}">
<script>
(function () {
  var anchors = ${scriptJson(anchors)};
  var target = ${scriptJson(target)};
  var id = decodeURIComponent((location.hash || '').replace(/^#/, ''));
  if (id && Object.prototype.hasOwnProperty.call(anchors, id)) {
    location.replace(${scriptJson(base)} + anchors[id] + '#' + encodeURIComponent(id));
  } else {
    location.replace(target + (location.hash || ''));
  }
})();
</script>
</head>
<body>
<p>This page moved to <a href="${escapeHtml(target)}">${escapeHtml(target)}</a>.</p>
</body>
</html>
`
}

async function main() {
  const problems = registryProblems()
  if (problems.length) {
    throw new Error(`write-redirects: redirect registry is invalid:\n  ${problems.join('\n  ')}`)
  }
  if (!(await exists(join(OUT, 'index.html')))) {
    throw new Error(`write-redirects: no export under ${OUT}; run \`npm run build\` first.`)
  }

  // Check every target against the real export before anything is written.
  for (const to of Object.values(PAGE_REDIRECTS)) {
    if (!(await routeExists(to))) {
      throw new Error(`write-redirects: redirect target is not exported: ${to}`)
    }
  }
  for (const [from, anchors] of Object.entries(MOVED_ANCHORS)) {
    if (!(await routeExists(from))) {
      throw new Error(`write-redirects: moved-anchor source is not exported: ${from}`)
    }
    for (const to of Object.values(anchors)) {
      if (!(await routeExists(to))) {
        throw new Error(`write-redirects: moved-anchor target is not exported: ${to}`)
      }
    }
  }

  for (const [from, to] of Object.entries(PAGE_REDIRECTS)) {
    const rel = from.replace(/^\/+/, '')
    const html = join(OUT, `${rel}.html`)
    if (await exists(html)) {
      throw new Error(`write-redirects: ${from} collides with a real exported page`)
    }
    const target = `${BASE}${to}`
    await mkdir(dirname(html), { recursive: true })
    await writeFile(html, stubHtml(from, to))
    await writeFile(join(OUT, `${rel}.md`), `# Moved\n\nThis page moved to ${SITE_ORIGIN}${to}.\n`)
    await writeFile(join(OUT, `${rel}.txt`), `Moved to ${SITE_ORIGIN}${to}\n`)
    console.log(`redirect ${from} -> ${target}`)
  }
  console.log(`write-redirects: ${Object.keys(PAGE_REDIRECTS).length} page stub(s) written.`)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((err) => {
    console.error(err.message ?? err)
    process.exit(1)
  })
}
