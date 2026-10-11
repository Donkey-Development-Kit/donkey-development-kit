import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  PYPI_JSON_URL,
  documentedVersion,
  latestPypiVersion,
  projectVersion,
  versionBanner,
} from './version.mjs'

const PYTHON = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'python')

test('the documented version is the package version', async () => {
  const documented = await documentedVersion(resolve(PYTHON, 'pyproject.toml'))
  const init = await readFile(resolve(PYTHON, 'src', 'donkey_kit', '__init__.py'), 'utf8')
  const match = init.match(/^__version__\s*=\s*"([^"]+)"/m)
  assert.ok(documented, 'pyproject.toml has no [project] version')
  assert.ok(match, '__init__.py has no __version__')
  assert.equal(documented, match[1])
})

test('only the [project] table version is read', () => {
  const text = [
    '[build-system]',
    'version = "9.9.9"',
    '[project]',
    'name = "donkey-kit"',
    'version = "0.1.2"',
    '[tool.other]',
    'version = "8.8.8"',
  ].join('\n')
  assert.equal(projectVersion(text), '0.1.2')
  assert.equal(projectVersion('[tool.x]\nversion = "1"\n'), null)
})

test('a missing pyproject.toml means no documented version', async () => {
  assert.equal(await documentedVersion('/nonexistent/pyproject.toml'), null)
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
