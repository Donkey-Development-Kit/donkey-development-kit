'use client'

import { useEffect } from 'react'

import { resolveMovedAnchor } from '../lib/redirects.mjs'

// Forwards an old `#anchor` to the page its section moved to (epic #1824).
//
// A page split keeps the page's URL (`X.mdx` -> `X/index.mdx`), so an old
// `/cli#donkey-init` still lands on /cli but the heading now lives on
// /cli/setup. If the hash matches no element on this page and the redirect
// registry knows where it went, replace the location. A local id always wins,
// so an anchor that still exists here is never forwarded. Renders nothing.
const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? ''

export function MovedAnchors() {
  useEffect(() => {
    const forward = () => {
      const { hash, pathname } = window.location
      if (!hash || hash === '#') return
      let id = hash.slice(1)
      try {
        id = decodeURIComponent(id)
      } catch {
        // keep the raw id
      }
      if (document.getElementById(id)) return
      const target = resolveMovedAnchor(pathname, hash, BASE)
      if (target) window.location.replace(`${BASE}${target}`)
    }
    forward()
    window.addEventListener('hashchange', forward)
    return () => window.removeEventListener('hashchange', forward)
  }, [])
  return null
}
