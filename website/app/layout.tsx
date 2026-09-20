import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import { Footer, Layout, Navbar } from 'nextra-theme-docs'
import { Head } from 'nextra/components'
import { getPageMap } from 'nextra/page-map'
import 'nextra-theme-docs/style.css'
import '../styles/globals.css'

const SITE_NAME = 'Donkey Development Kit'
const SITE_DESCRIPTION =
  'An SDK for Agent Fabric — governed model access from your own agent framework, with budget, refusals, telemetry and simulation on one skeleton.'

// next/image's unoptimized loader does not prefix basePath for /public assets,
// so reference the served copy under website/public/img/ with the base path
// applied manually — same convention as components/index.tsx.
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? ''

const inter = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-inter',
})

// Replaces theme.config.tsx `head()`: the Metadata API builds the same
// `${title} — ${SITE_NAME}` titles (root page keeps the bare site name via
// `default`) and the shared description / OpenGraph tags. Per-page descriptions
// come from each page's frontmatter, resolved by generateMetadata in the
// [[...mdxPath]] route.
export const metadata: Metadata = {
  title: {
    default: SITE_NAME,
    template: `%s — ${SITE_NAME}`,
  },
  description: SITE_DESCRIPTION,
  openGraph: {
    title: SITE_NAME,
    description: SITE_DESCRIPTION,
  },
}

const navbar = (
  <Navbar
    logo={
      <span
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          fontWeight: 700,
          letterSpacing: '-0.01em',
        }}
      >
        <img
          src={`${BASE_PATH}/img/ddk-logo-stacked-black.png`}
          alt=""
          width={28}
          height={28}
          style={{ borderRadius: 4 }}
        />
        {SITE_NAME}
      </span>
    }
    projectLink="https://github.com/Donkey-Development-Kit/donkey-development-kit"
  />
)

const footer = (
  <Footer>
    <span>
      Donkey Development Kit — an SDK <em>for</em> Agent Fabric. “Agent Fabric”,
      “Anypoint”, and “Omni Gateway” are Salesforce trademarks; this project is
      descriptive (the trademark/support boundary).
    </span>
  </Footer>
)

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const pageMap = await getPageMap()
  return (
    <html
      lang="en"
      dir="ltr"
      suppressHydrationWarning
      className={`${inter.variable} ${inter.className}`}
    >
      {/* Violet accent, close to the reference docs look (was theme.config `color`). */}
      <Head color={{ hue: 262, saturation: 90 }} />
      <body>
        <Layout
          navbar={navbar}
          footer={footer}
          pageMap={pageMap}
          docsRepositoryBase="https://github.com/Donkey-Development-Kit/donkey-development-kit/tree/main/website/content"
          sidebar={{ defaultMenuCollapseLevel: 1, toggleButton: true }}
          toc={{ backToTop: 'Scroll to top' }}
        >
          {children}
        </Layout>
      </body>
    </html>
  )
}
