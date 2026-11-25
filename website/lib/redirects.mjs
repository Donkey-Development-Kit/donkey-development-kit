// Permanent redirects for docs URLs that have moved (epic #1824, sub-issue #1825).
//
// Published `donkey-kit` wheels embed docs URLs forever (docstrings, error
// messages, READMEs), so every entry here is permanent. The site is a static
// export with no server redirects, so:
//
//   PAGE_REDIRECTS  — a whole page moved to a new URL. `write-redirects.mjs`
//                     writes a static stub at the old path that forwards there.
//   MOVED_ANCHORS   — a section moved off a page whose URL is unchanged (a page
//                     split turned `X.mdx` into `X/index.mdx`). Keyed by the
//                     old page path, then by the anchor id, valued by the new
//                     page path. `<MovedAnchors>` forwards those hashes on the
//                     client. A split's old page may also have a PAGE_REDIRECTS
//                     entry for its *other* anchors; MOVED_ANCHORS wins.
//
// Targets are root-relative, without basePath, without trailing slash and
// without `.html`. Entries are empty until the move/split sub-issues add them.

export const PAGE_REDIRECTS = {
  // #1827: A2A gathered into one section.
  '/tool-access/a2a': '/a2a/call',
  '/a2a-gateway-policies': '/a2a/gateway-policies',
}

export const MOVED_ANCHORS = {
  // #1827: /a2a became the A2A overview; the serving page moved to /a2a/serve.
  '/a2a': {
    'call-it-from-any-a2a-client': '/a2a/serve',
    'donkey-dev--a-gateway-in-front-of-your-laptop': '/a2a/serve',
    'donkey-expose--the-ingress-registered-from-code-live': '/a2a/serve',
    'donkeyserve--the-listener-in-one-line': '/a2a/serve',
    'governed-inbound': '/a2a/serve',
    'install-the-extra': '/a2a/serve',
    'mark-the-agent-and-serve-it': '/a2a/serve',
    'related': '/a2a/serve',
    'selecting-a-protocol-version': '/a2a/serve',
    'simulated-ingress': '/a2a/serve',
    'the-setup-it-replaces': '/a2a/serve',
    'what-the-card-says': '/a2a/serve',
    'what-the-handler-gets-and-returns': '/a2a/serve',
    'where-it-listens': '/a2a/serve',
    'why-not-have-users-call-the-a2a-sdk-directly': '/a2a/serve',
    'why-the-agent-still-runs-a-listener': '/a2a/serve',
  },
}

// Strip the basePath prefix, query, hash, trailing slash and `.html`, and
// return a root-relative route. Unknown input is returned normalised, never
// thrown on.
export function normalisePath(pathname, basePath = '') {
  let p = String(pathname || '/').split(/[?#]/)[0]
  const base = String(basePath || '').replace(/\/+$/, '')
  if (base && (p === base || p.startsWith(`${base}/`))) p = p.slice(base.length) || '/'
  p = p.replace(/\.html$/, '')
  if (p.length > 1) p = p.replace(/\/+$/, '')
  if (!p.startsWith('/')) p = `/${p}`
  return p || '/'
}

// `#id` -> 'id' (decoded). Empty or bare '#' -> ''.
export function idFromHash(hash) {
  const raw = String(hash || '').replace(/^#/, '')
  try {
    return decodeURIComponent(raw)
  } catch {
    return raw
  }
}

// The new location of one anchor that moved off a split page, or null.
// Returns the target page path with the same id as its fragment.
export function resolveMovedAnchor(pathname, hash, basePath = '', moved = MOVED_ANCHORS) {
  const id = idFromHash(hash)
  if (!id) return null
  const old = normalisePath(pathname, basePath)
  const target = moved[old]?.[id]
  return target ? `${target}#${encodeURIComponent(id)}` : null
}

// Where a request for an old page should go, keeping the fragment, or null.
// A moved anchor takes precedence over the page redirect, so an old
// `/a2a#donkey-expose` can point somewhere different from `/a2a`'s page target.
export function resolvePageRedirect(
  pathname,
  hash,
  basePath = '',
  pages = PAGE_REDIRECTS,
  moved = MOVED_ANCHORS
) {
  const old = normalisePath(pathname, basePath)
  const anchorTarget = resolveMovedAnchor(old, hash, '', moved)
  if (anchorTarget) return anchorTarget
  const target = pages[old]
  if (!target) return null
  return `${target}${hash && hash !== '#' ? hash : ''}`
}

// Static checks on the registry itself, used by the unit tests and the build.
// Returns a list of human-readable problems (empty when the registry is sound).
export function registryProblems(pages = PAGE_REDIRECTS, moved = MOVED_ANCHORS) {
  const problems = []
  const sources = new Set(Object.keys(pages))
  for (const [from, to] of Object.entries(pages)) {
    if (!from.startsWith('/') || from.endsWith('/') || from.endsWith('.html')) {
      problems.push(`PAGE_REDIRECTS source is not a normalised route: ${from}`)
    }
    if (!to.startsWith('/') || to.endsWith('/') || to.endsWith('.html') || to.includes('#')) {
      problems.push(`PAGE_REDIRECTS target is not a normalised route: ${from} -> ${to}`)
    }
    if (from === to) problems.push(`PAGE_REDIRECTS sends ${from} to itself`)
    if (sources.has(to)) problems.push(`PAGE_REDIRECTS chain: ${from} -> ${to} -> …`)
  }
  for (const [from, anchors] of Object.entries(moved)) {
    for (const [id, to] of Object.entries(anchors)) {
      if (!id) problems.push(`MOVED_ANCHORS ${from} has an empty anchor id`)
      if (!to.startsWith('/') || to.includes('#')) {
        problems.push(`MOVED_ANCHORS ${from}#${id} target is not a route: ${to}`)
      }
      if (to === from) problems.push(`MOVED_ANCHORS ${from}#${id} points at its own page`)
      if (sources.has(to)) problems.push(`MOVED_ANCHORS ${from}#${id} targets a redirected page: ${to}`)
    }
  }
  return problems
}
