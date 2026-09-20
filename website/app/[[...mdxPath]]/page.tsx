import { generateStaticParamsFor, importPage } from 'nextra/pages'
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
  return metadata
}

const Wrapper = getMDXComponents().wrapper

export default async function Page(props: PageProps) {
  const params = await props.params
  const result = await importPage(params.mdxPath)
  const { default: MDXContent, toc, metadata, sourceCode } = result
  return (
    <Wrapper toc={toc} metadata={metadata} sourceCode={sourceCode}>
      {/* Nextra 4 no longer wraps page markup in `.nextra-content`; this
          `.af-prose` div is the stable hook styles/globals.css keys the prose
          refinements off (see the typography section there). */}
      <div className="af-prose">
        <MDXContent {...props} params={params} />
      </div>
    </Wrapper>
  )
}
