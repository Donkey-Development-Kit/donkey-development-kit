import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import { dirname, join, relative, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { DOCS_REF, REPO_URL } from './repo.mjs'

const WEBSITE = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const PYPI_README = resolve(WEBSITE, '..', 'python', 'README.md')

// `${REPO_URL}/blob/<ref>/…` or `/tree/<ref>/…`; the trailing `/` keeps the
// sibling `-demos` repository out of the match.
const escapeRegExp = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const SDK_LINK = new RegExp(`${escapeRegExp(REPO_URL)}/(?:blob|tree)/([^/\\s)"'>]+)`, 'g')

async function readerFacingFiles() {
  const files = []
  for (const dir of ['content', 'components']) {
    for (const entry of await readdir(join(WEBSITE, dir), { recursive: true })) {
      if (/\.(mdx|tsx)$/.test(entry)) files.push(join(WEBSITE, dir, entry))
    }
  }
  return [...files, PYPI_README]
}

function offRefLinks(text) {
  return [...text.matchAll(SDK_LINK)].filter(m => m[1] !== DOCS_REF).map(m => m[0])
}

test(`reader-facing links into the SDK repository point at ${DOCS_REF}`, async () => {
  const offenders = []
  for (const file of await readerFacingFiles()) {
    for (const link of offRefLinks(await readFile(file, 'utf8'))) {
      offenders.push(`${relative(WEBSITE, file)}: ${link}`)
    }
  }
  assert.deepEqual(offenders, [])
})

test('the PyPI README has no relative links (PyPI does not resolve them)', async () => {
  const text = await readFile(PYPI_README, 'utf8')
  const relativeLinks = [
    ...text.matchAll(/\]\((?!https?:|mailto:|#)([^)]+)\)/g),
    ...text.matchAll(/\s(?:src|href)="(?!https?:|mailto:|#)([^"]+)"/g),
  ].map(m => m[1])
  assert.deepEqual(relativeLinks, [])
})

test('the ref guard catches a develop link and ignores the demos repository', () => {
  assert.deepEqual(offRefLinks(`[a](${REPO_URL}/blob/develop/docs/x.md)`), [
    `${REPO_URL}/blob/develop`,
  ])
  assert.deepEqual(offRefLinks(`[a](${REPO_URL}/tree/${DOCS_REF}/python)`), [])
  assert.deepEqual(offRefLinks(`[a](${REPO_URL}-demos/tree/develop/demos)`), [])
})
