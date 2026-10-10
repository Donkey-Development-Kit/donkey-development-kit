import { generateStaticParamsFor, importPage } from 'nextra/pages'
import { Badge } from '../../components'
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
  return rest
}

const Wrapper = getMDXComponents().wrapper

export default async function Page(props: PageProps) {
  const params = await props.params
  const result = await importPage(params.mdxPath)
  const { default: MDXContent, toc, metadata, sourceCode } = result
  // Every page's status comes from its frontmatter (#797); statusOf throws on a
  // missing or unknown value, so `next build` fails rather than shipping a page
  // with no status.
  const page = statusOf(metadata, `content/${(params.mdxPath ?? ['index']).join('/')}`)
  return (
    <Wrapper toc={toc} metadata={metadata} sourceCode={sourceCode}>
      {/* Nextra 4 no longer wraps page markup in `.nextra-content`; this
          `.af-prose` div is the stable hook styles/globals.css keys the prose
          refinements off (see the typography section there). */}
      <div className="af-prose">
        {page.badge && (
          <p className="af-page-status" title={page.summary}>
            <Badge tone={page.tone}>{page.label}</Badge>
          </p>
        )}
        <MDXContent {...props} params={params} />
      </div>
    </Wrapper>
  )
}
