export const CLOUDFLARE_BEACON_SRC = 'https://static.cloudflareinsights.com/beacon.min.js'

const SITE_TOKEN = /^[0-9a-f]{32}$/

/**
 * Cloudflare Web Analytics beacon attributes for a build-time site token.
 *
 * Returns null for an absent or malformed token so local and unconfigured
 * builds ship no analytics and a bad value is never rendered into the page.
 *
 * @param {string | undefined} token
 * @returns {{ src: string, 'data-cf-beacon': string } | null}
 */
export function cloudflareBeacon(token) {
  if (typeof token !== 'string' || !SITE_TOKEN.test(token)) return null
  return {
    src: CLOUDFLARE_BEACON_SRC,
    'data-cf-beacon': JSON.stringify({ token }),
  }
}
