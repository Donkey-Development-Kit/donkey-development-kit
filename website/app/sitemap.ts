import type { MetadataRoute } from 'next'
import { generateStaticParamsFor } from 'nextra/pages'
import { pagePath, siteBase } from '../lib/site.mjs'

export const dynamic = 'force-static' // required by `output: 'export'`

// One entry per page the catch-all route renders, enumerated the same way as
// its generateStaticParams, so the sitemap cannot drift from the built pages.
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const params = (await generateStaticParamsFor('mdxPath')()) as { mdxPath?: string[] }[]
  const base = siteBase()
  return params
    .map(({ mdxPath }) => pagePath(mdxPath))
    .sort()
    .map((path) => ({ url: new URL(path.slice(1), base).toString() }))
}
