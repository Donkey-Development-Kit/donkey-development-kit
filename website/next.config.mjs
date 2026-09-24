import nextra from 'nextra'

// Nextra 4 is App-Router only: `theme` / `themeConfig` are gone — the docs theme
// is composed from <Layout>/<Navbar>/<Footer> in app/layout.tsx instead. Search
// moved from FlexSearch to Pagefind, indexed by the `postbuild` step against out/.
// Code blocks use one dark palette (VS Code Dark+) in both site themes.
// Options must stay plain data: Turbopack serialises loader options.
const withNextra = nextra({
  defaultShowCopyCode: true,
  mdxOptions: {
    rehypePrettyCodeOptions: {
      theme: { light: 'dark-plus', dark: 'dark-plus' },
    },
  },
})

// Project sub-path for GitHub Pages (e.g. "/donkey-development-kit"). Left empty for
// `npm run dev` and for a future custom domain, so local/root hosting is
// unaffected; the Pages workflow sets DOCS_BASE_PATH=/donkey-development-kit.
const basePath = process.env.DOCS_BASE_PATH ?? ''

export default withNextra({
  reactStrictMode: true,
  output: 'export', // static HTML export — GitHub Pages has no Node server
  images: { unoptimized: true }, // the next/image optimizer can't run on a static host
  basePath,
  assetPrefix: basePath || undefined,
  // Expose the sub-path to component code: next/image's unoptimized loader does
  // NOT apply basePath to /public assets, so <Figure> prefixes them itself.
  env: { NEXT_PUBLIC_BASE_PATH: basePath },
})
