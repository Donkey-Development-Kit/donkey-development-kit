import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  PYPI_JSON_URL,
  documentedVersion,
  latestPypiVersion,
  packageVersion,
  versionBanner,
} from './version.mjs'

const PYTHON = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'python')

test('the documented version is the package version', async () => {
  const documented = await documentedVersion(resolve(PYTHON, 'src', 'donkey_kit', '__init__.py'))
  const pyproject = await readFile(resolve(PYTHON, 'pyproject.toml'), 'utf8')
  assert.ok(documented, '__init__.py has no __version__')
  // #767: the version is declared once, in __init__.py; pyproject.toml reads it.
  assert.match(pyproject, /^dynamic = \["version"\]$/m)
})

test('only a module-level __version__ assignment is read', () => {
  const text = [
    '"""donkey-kit."""',
    '    __version__ = "9.9.9"',
    '__version__ = "0.1.2"',
  ].join('\n')
  assert.equal(packageVersion(text), '0.1.2')
  assert.equal(packageVersion('VERSION = "1"\n'), null)
})

test('a missing __init__.py means no documented version', async () => {
  assert.equal(await documentedVersion('/nonexistent/__init__.py'), null)
})

test('the latest PyPI version comes from info.version', async () => {
  let asked
  const fetchImpl = async (url) => {
    asked = url
    return { ok: true, json: async () => ({ info: { version: '0.1.3' } }) }
  }
  assert.equal(await latestPypiVersion({ fetchImpl }), '0.1.3')
  assert.equal(asked, PYPI_JSON_URL)
})

test('a failed, non-OK or malformed PyPI answer leaves the latest version unknown', async () => {
  const failing = async () => {
    throw new Error('offline')
  }
  const notOk = async () => ({ ok: false, json: async () => ({}) })
  const malformed = async () => ({ ok: true, json: async () => ({ info: {} }) })
  for (const fetchImpl of [failing, notOk, malformed]) {
    assert.equal(await latestPypiVersion({ fetchImpl }), null)
  }
})

test('the banner names the documented version, and the latest one when it differs', () => {
  assert.equal(versionBanner(null, '0.1.2'), null)
  assert.deepEqual(versionBanner('0.1.2', '0.1.2'), {
    text: 'These docs describe donkey-kit v0.1.2.',
    storageKey: 'ddk-docs-version-0.1.2-0.1.2',
  })
  assert.deepEqual(versionBanner('0.1.2', null), {
    text: 'These docs describe donkey-kit v0.1.2.',
    storageKey: 'ddk-docs-version-0.1.2-unknown',
  })
  assert.equal(
    versionBanner('0.1.2', '0.1.3').text,
    'These docs describe donkey-kit v0.1.2. The latest release on PyPI is v0.1.3.',
  )
})
