import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

// The version banner (#795): which `donkey-kit` version these docs describe,
// and the latest release on PyPI when that is a different one.
//
// The documented version is read from the SDK's own pyproject.toml at build
// time, so the site and the package it describes share one version source.
// The latest PyPI version is fetched at build time too; a failed or slow fetch
// leaves it unknown and the banner shows the documented version alone.

export const PYPI_JSON_URL = 'https://pypi.org/pypi/donkey-kit/json'
export const RELEASES_URL =
  'https://github.com/Donkey-Development-Kit/donkey-development-kit/releases'

// website/ is the working directory for `next build` and `npm test`.
const PYPROJECT = resolve(process.cwd(), '..', 'python', 'pyproject.toml')

/**
 * The `version` of the `[project]` table in a pyproject.toml.
 *
 * @param {string} text
 * @returns {string | null}
 */
export function projectVersion(text) {
  const start = text.search(/^\[project\]\s*$/m)
  if (start === -1) return null
  const rest = text.slice(start).split('\n').slice(1).join('\n')
  const end = rest.search(/^\[/m)
  const table = end === -1 ? rest : rest.slice(0, end)
  const match = table.match(/^version\s*=\s*"([^"]+)"/m)
  return match ? match[1] : null
}

/**
 * The version these docs describe, or null when the SDK sources are absent.
 *
 * @param {string} [path]
 * @returns {Promise<string | null>}
 */
export async function documentedVersion(path = PYPROJECT) {
  try {
    return projectVersion(await readFile(path, 'utf8'))
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
