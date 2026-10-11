import type { MetadataRoute } from 'next'
import { siteBase } from '../lib/site.mjs'

export const dynamic = 'force-static' // required by `output: 'export'

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: '*', allow: '/' },
    sitemap: new URL('sitemap.xml', siteBase()).toString(),
  }
}
