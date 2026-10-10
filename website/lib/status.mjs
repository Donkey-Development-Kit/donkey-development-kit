// The page-status vocabulary (#797). Every page under content/ declares one of
// these in its frontmatter (`status: live`), and that one value drives both the
// badge rendered at the top of the page (app/[[...mdxPath]]/page.tsx) and the
// status shown in llms.txt and the per-page .md files (scripts/generate-llms.mjs).
// Pages no longer hand-write a page-level <Badge>.
//
// The values follow the verification ledger, docs/verified-apis.md (§0.3):
//   live              the API the page documents ships, and the gateway contract
//                     it rests on is VERIFIED (LIVE) or VERIFIED (CLI) there.
//   offline-verified  ships and is proven offline (conformance suite against the
//                     simulator, or verify_frameworks.py signature checks), but
//                     has had no real round-trip through a governed proxy yet;
//                     the ledger row still reads UNVERIFIED (signature confirmed).
//   roadmap           planned design: the API is not shipped, or it raises
//                     `_verify.blocked(...)` until its ledger row is confirmed.
//
// python/scripts/check_doc_snippets.py reads the same frontmatter: it skips
// `roadmap` pages and fails a `live` page that calls a blocked API.
/** @type {Record<'live' | 'offline-verified' | 'roadmap', {label: string, tone: 'live' | 'roadmap' | 'neutral', summary: string}>} */
export const STATUSES = {
  live: {
    label: 'Live',
    tone: 'live',
    summary: 'Shipped, and verified against a real governed gateway.',
  },
  'offline-verified': {
    label: 'Offline-verified',
    tone: 'neutral',
    summary: 'Shipped and verified offline; no live gateway round-trip yet.',
  },
  roadmap: {
    label: 'Roadmap',
    tone: 'roadmap',
    summary: 'Planned design; not shipped yet.',
  },
}

// `statusBadge: false` in a page's frontmatter renders no badge, and the page's
// llms.txt entry and .md carry no status either. It is for pages that document
// no SDK surface: the landing page, the roadmap and the community pages.
/**
 * @param {Record<string, unknown> | undefined} frontmatter
 * @param {string} [where]
 */
export function statusOf(frontmatter, where = 'page') {
  const status = /** @type {keyof typeof STATUSES} */ (frontmatter?.status)
  if (typeof status !== 'string' || !Object.hasOwn(STATUSES, status)) {
    const allowed = Object.keys(STATUSES).join(', ')
    throw new Error(`${where}: frontmatter status must be one of ${allowed}, got ${JSON.stringify(status)}`)
  }
  return { status, ...STATUSES[status], badge: String(frontmatter?.statusBadge) !== 'false' }
}
