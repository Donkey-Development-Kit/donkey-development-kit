import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

// The version banner (#795): which `donkey-kit` version these docs describe,
// and the latest release on PyPI when that is a different one.
//
// The documented version is read at build time from `__version__` in the
// SDK's `__init__.py`, the package's one version source (#767: pyproject.toml
// declares the version dynamic), so the site and the package it describes
// cannot disagree. The latest PyPI version is fetched at build time too; a
// failed or slow fetch leaves it unknown and the banner shows the documented
// version alone.

export const PYPI_JSON_URL = 'https://pypi.org/pypi/donkey-kit/json'
export const RELEASES_URL =
  'https://github.com/Donkey-Development-Kit/donkey-development-kit/releases'

// website/ is the working directory for `next build` and `npm test`.
const PACKAGE_INIT = resolve(process.cwd(), '..', 'python', 'src', 'donkey_kit', '__init__.py')

/**
 * The module-level `__version__ = "..."` assignment in an `__init__.py`.
 *
 * @param {string} text
 * @returns {string | null}
 */
export function packageVersion(text) {
  const match = text.match(/^__version__\s*=\s*"([^"]+)"/m)
  return match ? match[1] : null
}

/**
 * The version these docs describe, or null when the SDK sources are absent.
 *
 * @param {string} [path]
 * @returns {Promise<string | null>}
 */
export async function documentedVersion(path = PACKAGE_INIT) {
  try {
    return packageVersion(await readFile(path, 'utf8'))
  } catch {
    return null
  }
}

/**
 * The latest release on PyPI, or null when PyPI can't be read in time.
 *
 * @param {{ fetchImpl?: typeof fetch, timeoutMs?: number }} [options]
 * @returns {Promise<string | null>}
 */
export async function latestPypiVersion({ fetchImpl = fetch, timeoutMs = 3000 } = {}) {
  try {
    const response = await fetchImpl(PYPI_JSON_URL, {
      signal: AbortSignal.timeout(timeoutMs),
      headers: { accept: 'application/json' },
    })
    if (!response.ok) return null
    const body = await response.json()
    const version = body?.info?.version
    return typeof version === 'string' && version ? version : null
  } catch {
    return null
  }
}

/**
 * What the banner says, or null when there is no documented version.
 *
 * @param {string | null} documented
 * @param {string | null} latest
 * @returns {{ text: string, storageKey: string } | null}
 */
export function versionBanner(documented, latest) {
  if (!documented) return null
  let text = `These docs describe donkey-kit v${documented}.`
  if (latest && latest !== documented) {
    text += ` The latest release on PyPI is v${latest}.`
  }
  return { text, storageKey: `ddk-docs-version-${documented}-${latest ?? 'unknown'}` }
}
