export const SITE_NAME = 'Donkey Development Kit'

// The canonical origin of the deployed docs (public/CNAME). Overridable for
// previews and forks; the optional DOCS_BASE_PATH sub-path (next.config.mjs)
// is appended so absolute URLs match where the export is actually served.
export const SITE_ORIGIN = 'https://docs.donkey-kit.dev'

export function siteBase(origin = process.env.NEXT_PUBLIC_SITE_URL, basePath = process.env.DOCS_BASE_PATH) {
  const host = (origin || SITE_ORIGIN).replace(/\/+$/, '')
  const prefix = (basePath ?? '').replace(/^\/*/, '/').replace(/\/+$/, '')
  return `${host}${prefix === '/' ? '' : prefix}/`
}

// Site-relative route for a catch-all `mdxPath` (Next joins it onto the
// metadataBase path). The root page is `/`; other pages have no trailing slash,
// matching the `<route>.html` files Next's static export writes.
export function pagePath(mdxPath) {
  const segments = (mdxPath ?? []).filter(Boolean)
  return segments.length ? `/${segments.join('/')}` : '/'
}

// Served from app/opengraph-image.jpg (Next's file convention, static export).
// Page-level `openGraph` replaces the layout's wholesale, so it is restated.
export const OG_IMAGE = { url: '/opengraph-image.jpg', width: 1200, height: 675, alt: SITE_NAME }

// Per-page metadata from a Nextra page's own `metadata` (frontmatter title and
// description). Every page gets a unique title, canonical URL and og:*/twitter:*
// tags; the root layout only supplies the site-wide defaults.
export function pageMetadata(metadata, mdxPath) {
  const isRoot = !(mdxPath ?? []).length
  let title = typeof metadata.title === 'string' ? metadata.title : undefined
  // content/examples/<x> reuses the heading of content/frameworks/<x> (and
  // budget, ...), which would give two pages the same title.
  if (title && mdxPath?.[0] === 'examples' && mdxPath.length > 1) title = `Examples: ${title}`
  // The home page keeps the bare site name (no "Index — " from the filename).
  const fullTitle = isRoot || !title ? SITE_NAME : `${title} — ${SITE_NAME}`
  const canonical = pagePath(mdxPath)
  const description = metadata.description ?? undefined
  return {
    ...metadata,
    title: isRoot ? { absolute: SITE_NAME } : title,
    alternates: { canonical },
    openGraph: {
      type: 'website',
      siteName: SITE_NAME,
      url: canonical,
      title: fullTitle,
      description,
      images: [OG_IMAGE],
    },
    twitter: { card: 'summary_large_image', title: fullTitle, description, images: [OG_IMAGE.url] },
  }
}
