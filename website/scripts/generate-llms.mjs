// Generate the AI-readable docs artifacts from the Nextra pages (#205, BG §1.10):
//
//   public/llms.txt        — llmstxt.org index: sectioned list of links to the .md pages
//   public/llms-full.txt   — every page's markdown inlined into one file
//   public/<path>.md        — raw markdown for each page, served beside its .html
//
// Source of truth is the pages themselves: ordering + titles come from the
// `_meta.js` tree, content + description from each `.mdx` and its frontmatter.
// This script owns NO copy of the content — it only transforms what already
// ships on the site, so it can never invent an endpoint/header/class name the
// pages don't already document (verification discipline). CI regenerates and fails on any diff
// (see .github/workflows/ci.yml), so the committed artifacts cannot drift.
//
// Run: `npm run generate:llms` (also runs automatically via `prebuild`).

import { readFile, writeFile, mkdir, access } from 'node:fs/promises'
import { dirname, join, resolve, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const WEBSITE = resolve(HERE, '..')
const CONTENT = join(WEBSITE, 'content')
const PUBLIC = join(WEBSITE, 'public')

// The deployed GitHub Pages URL. The site is served under the project sub-path
// (basePath = /donkey-development-kit); the true domain-root /llms.txt waits on
// a custom domain (#205 ACs). Links therefore carry the full deployed prefix.
const SITE = 'https://donkey-development-kit.github.io/donkey-development-kit'

const TITLE = 'Donkey Development Kit'
const TAGLINE =
  'An SDK for consuming Agent Fabric governance from Python agent code, without adopting Mule.'

async function exists(p) {
  try {
    await access(p)
    return true
  } catch {
    return false
  }
}

// --- _meta.js reading -------------------------------------------------------
// The _meta.js files are `export default { ... }` object literals (string
// titles + `{type:'separator'}` markers). The website package is CommonJS, so
// a plain `import()` of the ESM-syntax .js would fail — importing the source as
// a data: URL module always evaluates it as ESM, which is what Nextra assumes.
async function readMeta(dir) {
  const metaPath = join(dir, '_meta.js')
  if (!(await exists(metaPath))) return null
  const src = await readFile(metaPath, 'utf8')
  const mod = await import(`data:text/javascript,${encodeURIComponent(src)}`)
  return mod.default
}

// --- page collection --------------------------------------------------------
// Walk the _meta.js tree into an ordered, flat page list. Each page records the
// section it belongs under (a top-level separator title, or the label of the
// sub-directory it lives in) for grouping in llms.txt.
async function collectPages(dir, urlPrefix, section, pages) {
  const meta = await readMeta(dir)
  if (!meta) return
  let currentSection = section
  for (const [key, value] of Object.entries(meta)) {
    if (value && typeof value === 'object' && value.type === 'separator') {
      currentSection = value.title || currentSection
      continue
    }
    const title = typeof value === 'string' ? value : value?.title || key
    const mdxPath = join(dir, `${key}.mdx`)
    const subDir = join(dir, key)

    if (key === 'index') {
      // Directory root page: url is the directory itself ('' for the site root).
      pages.push({ title, urlPath: urlPrefix, file: mdxPath, section: currentSection })
    } else if (await exists(mdxPath)) {
      const urlPath = urlPrefix ? `${urlPrefix}/${key}` : key
      pages.push({ title, urlPath, file: mdxPath, section: currentSection })
    } else if (await exists(join(subDir, '_meta.js'))) {
      // A sub-directory: recurse, and group its pages under its own nav label.
      await collectPages(subDir, urlPrefix ? `${urlPrefix}/${key}` : key, title, pages)
    }
  }
}

// --- mdx -> markdown transform ---------------------------------------------
function stripFrontmatter(src) {
  const m = src.match(/^---\n([\s\S]*?)\n---\n?/)
  if (!m) return { frontmatter: {}, body: src }
  const frontmatter = {}
  for (const line of m[1].split('\n')) {
    const kv = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/)
    if (kv) frontmatter[kv[1]] = kv[2].replace(/^["']|["']$/g, '').trim()
  }
  return { frontmatter, body: src.slice(m[0].length) }
}

// Split into fenced-code vs prose segments so the prose cleanup never touches
// code. This is essential: code blocks legitimately contain `import openai` /
// `import Anthropic from "@anthropic-ai/sdk"` lines that must survive verbatim.
function splitFences(src) {
  const lines = src.split('\n')
  const segments = []
  let buf = []
  let inFence = false
  const flush = (type) => {
    segments.push({ type, text: buf.join('\n') })
    buf = []
  }
  for (const line of lines) {
    const isFence = /^\s*(```|~~~)/.test(line)
    if (isFence && !inFence) {
      flush('text')
      inFence = true
      buf.push(line)
    } else if (isFence && inFence) {
      buf.push(line)
      flush('code')
      inFence = false
    } else {
      buf.push(line)
    }
  }
  flush(inFence ? 'code' : 'text')
  return segments
}

function parseMdxImports(body, fileDir) {
  const map = {}
  const re = /^import\s+(\w+)\s+from\s+['"](.+?\.mdx)['"]/gm
  let m
  while ((m = re.exec(body)) !== null) map[m[1]] = resolve(fileDir, m[2])
  return map
}

function rewriteLinks(text, pageSet) {
  // Root-relative internal links (`](/errors)`, `](/concepts/verification#x)`)
  // -> the deployed .md URL, so an agent following a link fetches markdown.
  return text.replace(/\]\(\/([^)\s]*)\)/g, (whole, rest) => {
    const [path, hash] = rest.split('#')
    const clean = path.replace(/\/$/, '')
    if (clean && pageSet.has(clean)) return `](${SITE}/${clean}.md${hash ? `#${hash}` : ''})`
    return `](${SITE}/${clean}${hash ? `#${hash}` : ''})`
  })
}

function cleanProse(text, { partials, pageSet }) {
  let out = text
  // MDX comments {/* ... */}
  out = out.replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
  // ESM import/export statements (safe: we are outside code fences here)
  out = out
    .split('\n')
    .filter((l) => !/^\s*(import|export)\b/.test(l))
    .join('\n')
  // Inline imported .mdx partials, e.g. <OpenAiProxyTs /> -> its markdown.
  for (const [name, content] of Object.entries(partials)) {
    out = out.replace(new RegExp(`<${name}\\s*/>`, 'g'), `\n${content}\n`)
  }
  // Drop remaining JSX component tags (uppercase), keeping their children.
  // `[^>]` matches newlines, so multi-line prop lists collapse too.
  out = out
    .replace(/<[A-Z][A-Za-z0-9.]*(\s[^>]*?)?\/>/g, '') // self-closing
    .replace(/<[A-Z][A-Za-z0-9.]*(\s[^>]*?)?>/g, '') // opening
    .replace(/<\/[A-Z][A-Za-z0-9.]*>/g, '') // closing
  out = rewriteLinks(out, pageSet)
  return out
}

async function transformFile(file, pageSet, seen = new Set()) {
  const abs = resolve(file)
  if (seen.has(abs)) return '' // cycle guard for partials
  seen.add(abs)
  const raw = await readFile(abs, 'utf8')
  const { body } = stripFrontmatter(raw)
  const imports = parseMdxImports(body, dirname(abs))

  // Transform imported partials first so they can be inlined.
  const partials = {}
  for (const [name, ppath] of Object.entries(imports)) {
    if (await exists(ppath)) partials[name] = (await transformFile(ppath, pageSet, seen)).trim()
  }

  const segments = splitFences(body)
  const rendered = segments
    .map((seg) => (seg.type === 'code' ? seg.text : cleanProse(seg.text, { partials, pageSet })))
    .join('\n')

  return `${rendered.replace(/\n{3,}/g, '\n\n').trim()}\n`
}

function firstParagraph(markdown) {
  for (const block of markdown.split('\n\n')) {
    const t = block.trim()
    if (!t || t.startsWith('#') || t.startsWith('```')) continue
    return t.replace(/\s+/g, ' ').trim()
  }
  return ''
}

function truncate(s, n) {
  return s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s
}

async function main() {
  const pages = []
  await collectPages(CONTENT, '', 'Getting started', pages)

  const pageSet = new Set(pages.map((p) => p.urlPath).filter(Boolean))

  // Transform every page and resolve its description.
  for (const page of pages) {
    if (!(await exists(page.file))) {
      throw new Error(`_meta.js references a missing page: ${relative(WEBSITE, page.file)}`)
    }
    const { frontmatter } = stripFrontmatter(await readFile(page.file, 'utf8'))
    page.markdown = await transformFile(page.file, pageSet)
    page.description = frontmatter.description || truncate(firstParagraph(page.markdown), 200)
    page.url = `${SITE}/${page.urlPath || 'index'}`
    page.mdUrl = `${page.url}.md`
  }

  // 1) per-page .md
  for (const page of pages) {
    const rel = `${page.urlPath || 'index'}.md`
    const dest = join(PUBLIC, rel)
    await mkdir(dirname(dest), { recursive: true })
    await writeFile(dest, page.markdown)
  }

  // 2) llms.txt — the llmstxt.org index, grouped by section in nav order.
  const indexLines = [`# ${TITLE}`, '', `> ${TAGLINE}`]
  let section = null
  for (const page of pages) {
    if (page.section !== section) {
      section = page.section
      indexLines.push('', `## ${section}`, '')
    }
    const desc = page.description ? `: ${page.description}` : ''
    indexLines.push(`- [${page.title}](${page.mdUrl})${desc}`)
  }
  indexLines.push('')
  await writeFile(join(PUBLIC, 'llms.txt'), indexLines.join('\n'))

  // 3) llms-full.txt — every page inlined, so an assistant handed this file
  //    alone can emit correct code without following links.
  const fullParts = [
    `# ${TITLE} — full documentation`,
    '',
    `> ${TAGLINE}`,
    '',
    `Source: ${SITE}/  ·  Index: ${SITE}/llms.txt`,
    '',
  ]
  for (const page of pages) {
    fullParts.push('---', '', `Source: ${page.mdUrl}`, '', page.markdown.trim(), '')
  }
  await writeFile(join(PUBLIC, 'llms-full.txt'), `${fullParts.join('\n')}\n`)

  console.log(
    `Generated llms.txt, llms-full.txt, and ${pages.length} per-page .md files under website/public/.`
  )
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
