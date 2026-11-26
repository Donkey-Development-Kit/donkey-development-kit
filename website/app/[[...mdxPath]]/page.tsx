import { generateStaticParamsFor, importPage } from 'nextra/pages'
import { Badge } from '../../components'
import { MovedAnchors } from '../../components/moved-anchors'
import { pageMetadata } from '../../lib/site.mjs'
import { statusOf } from '../../lib/status.mjs'
import { useMDXComponents as getMDXComponents } from '../../mdx-components'

// Catch-all route that renders every .mdx under content/. generateStaticParams
// enumerates all routes at build time, which is what makes `output: 'export'`
// (static GitHub Pages) work.
export const generateStaticParams = generateStaticParamsFor('mdxPath')

type PageProps = {
  params: Promise<{ mdxPath?: string[] }>
}

export async function generateMetadata(props: PageProps) {
  const params = await props.params
  const { metadata } = await importPage(params.mdxPath)
  // `status` / `statusBadge` drive the page badge (lib/status.mjs); they are not
  // Next.js metadata fields, so they stay out of the <head>.
  const { status: _status, statusBadge: _statusBadge, ...rest } = metadata as typeof metadata & {
    status?: unknown
    statusBadge?: unknown
  }
  return pageMetadata(rest, params.mdxPath)
}

const Wrapper = getMDXComponents().wrapper

// In `next dev`, Nextra fills `metadata.timestamp` from git, and the docs theme's
// ClientWrapper then clones its `lastUpdated` element with the date. That element
// resolves to `undefined` during dev server rendering, so every page returns 500
// ("Element type is invalid", #1850). Dropping the timestamp in dev skips the
// "Last updated" line there; the production export still renders it.
const IS_DEV = process.env.NODE_ENV === 'development'

export default async function Page(props: PageProps) {
  const params = await props.params
  const result = await importPage(params.mdxPath)
  const { default: MDXContent, toc, metadata, sourceCode } = result
  // Every page's status comes from its frontmatter (#797); statusOf throws on a
  // missing or unknown value, so `next build` fails rather than shipping a page
  // with no status.
  const page = statusOf(metadata, `content/${(params.mdxPath ?? ['index']).join('/')}`)
  return (
    <Wrapper
      toc={toc}
      metadata={IS_DEV ? { ...metadata, timestamp: undefined } : metadata}
      sourceCode={sourceCode}
    >
      {/* Nextra 4 no longer wraps page markup in `.nextra-content`; this
          `.af-prose` div is the stable hook styles/globals.css keys the prose
          refinements off (see the typography section there). */}
      <div className="af-prose">
        {page.badge && (
          <p className="af-page-status" title={page.summary}>
            <Badge tone={page.tone}>{page.label}</Badge>
          </p>
        )}
        <MovedAnchors />
        <MDXContent {...props} params={params} />
      </div>
    </Wrapper>
  )
}
