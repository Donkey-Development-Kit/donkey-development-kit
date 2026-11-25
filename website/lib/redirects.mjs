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
  // #1830: the simulator's MCP and A2A sections moved to their own pages.
  '/simulator': {
    'a2a-ingress-replay': '/simulator/a2a',
    'low-level-helper-for-a-hand-built-toolset': '/simulator/mcp',
    'mcp-tool-fault-injection': '/simulator/mcp',
    'outbound-a2a-fault-injection': '/simulator/a2a',
    'packaged-mcp-captures': '/simulator/mcp',
    'reusable-offline-baseline': '/simulator/mcp',
  },
  // #1831: correlation, cost attribution and routing moved off the telemetry page.
  '/telemetry': {
    'across-agent-hops': '/correlation',
    'correlation-ids': '/correlation',
    'run-ledger': '/correlation',
    'the-decorator-form': '/correlation',
    'cost-attribution-tags': '/cost-attribution',
    'per-request-attribution': '/cost-attribution',
    'the-question-this-answers': '/cost-attribution',
    'where-the-tags-go': '/cost-attribution',
    'routing--resilience': '/frameworks/routing',
    'semantic-caching--semantic-routing-live': '/frameworks/routing',
    'two-behaviours-worth-knowing': '/frameworks/routing',
    'typescript-parity-roadmap': '/frameworks/routing',
    'when-last_call-is-unavailable': '/frameworks/routing',
  },
  // #1833: the CLI page's decorator and workflow sections moved to their own pages.
  '/cli': {
    'decorators': '/decorators',
    'donkey-dev': '/cli/local',
    'donkey-doctor': '/cli/setup',
    'donkey-expose': '/cli/ship',
    'donkey-init': '/cli/setup',
    'donkey-mock': '/cli/local',
    'donkey-publish': '/cli/ship',
    'donkey-scaffold': '/cli/ship',
    'donkey-scan': '/cli/ship',
    'donkey-test': '/cli/local',
    'donkey-verify': '/cli/ship',
    'donkeyagent': '/decorators',
    'donkeygoverned': '/decorators',
    'donkeytool': '/decorators',
    'filling-a-placeholder-with---set': '/cli/ship',
    'from-code-to-a-publish-plan': '/cli/ship',
    'what-each-workflow-still-needs': '/cli/ship',
  },
  // #1834: the CI recipes and the Publication object moved off the publishing page.
  '/publishing': {
    'derivation-modes': '/publishing/publication',
    'descriptorauto--deriving-the-spec-from-code-roadmap': '/publishing/publication',
    'from-bitbucket-pipelines': '/publishing/ci',
    'from-github-actions': '/publishing/ci',
    'from-gitlab-ci': '/publishing/ci',
    'the-publication-object': '/publishing/publication',
    'verifying-at-runtime-verify': '/publishing/publication',
  },
  // #1835: the framework helpers and the gateway step-up sections moved to their own pages.
  '/hitl': {
    'a2a-authorization-code-challenges': '/hitl/gateway-step-up',
    'discovered-tools-local-round-trip': '/hitl/gateway-step-up',
    'gateway-step-up': '/hitl/gateway-step-up',
    'in-a-google-adk-app': '/hitl/frameworks',
    'in-a-langgraph-graph': '/hitl/frameworks',
    'in-a-strands-agent': '/hitl/frameworks',
    'in-an-openai-agents-sdk-run': '/hitl/frameworks',
    'step-up-on-a-tool-call': '/hitl/gateway-step-up',
    'the-framework-approval-helpers': '/hitl/frameworks',
    'waiting-while-listing-mcp-tools': '/hitl/gateway-step-up',
  },
  // #1836: the auth, attribution and control-plane sections moved to their own pages.
  '/reference/configuration': {
    'behaviour': '/reference/configuration/attribution',
    'correlation-headers': '/reference/configuration/attribution',
    'cost-attribution-tags': '/reference/configuration/attribution',
    'header-names-you-cant-use': '/reference/configuration/attribution',
    'invalid-values': '/reference/configuration/attribution',
    'optional-attribution': '/reference/configuration/attribution',
    'telemetry': '/reference/configuration/attribution',
    'tool-set-size-warnings': '/reference/configuration/attribution',
    'auth-providers': '/reference/configuration/auth',
    'bearer-token-auth-mode': '/reference/configuration/auth',
    'bring-your-own-provider-key': '/reference/configuration/auth',
    'governed-model-access': '/reference/configuration/auth',
    'jwt--model-wallet-auth-mode': '/reference/configuration/auth',
    'anypoint-control-plane': '/reference/configuration/control-plane',
    'credentials': '/reference/configuration/control-plane',
    'credentials-go-only-to-checked-endpoints': '/reference/configuration/control-plane',
    'other-headers': '/reference/configuration/control-plane',
    'what-the-sdk-sends-where': '/reference/configuration/control-plane',
    'which-features-use-the-control-plane': '/reference/configuration/control-plane',
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
