import type { MDXComponents } from 'nextra/mdx-components'
import { useMDXComponents as getDocsMDXComponents } from 'nextra-theme-docs'

// Nextra 4 requires this root file (Next.js's MDX convention picks it up). It
// merges the docs-theme MDX components with any per-page overrides. The site's
// own presentation components (Hero/Card/CardGrid/Figure/Badge) are still
// imported explicitly at the top of each .mdx from ../components, exactly as
// under Nextra 3, so they are not registered globally here.
const docsComponents = getDocsMDXComponents()

export function useMDXComponents(components?: MDXComponents) {
  return {
    ...docsComponents,
    ...components,
  }
}
