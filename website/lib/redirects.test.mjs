import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  MOVED_ANCHORS,
  PAGE_REDIRECTS,
  idFromHash,
  normalisePath,
  registryProblems,
  resolveMovedAnchor,
  resolvePageRedirect,
} from './redirects.mjs'

test('normalisePath strips query, hash, trailing slash and .html', () => {
  assert.equal(normalisePath('/cli/?x=1#y'), '/cli')
  assert.equal(normalisePath('/cli.html'), '/cli')
  assert.equal(normalisePath('/'), '/')
  assert.equal(normalisePath(''), '/')
  assert.equal(normalisePath('/a2a/call/'), '/a2a/call')
})

test('normalisePath strips the basePath prefix only at a segment boundary', () => {
  assert.equal(normalisePath('/donkey/cli', '/donkey'), '/cli')
  assert.equal(normalisePath('/donkey', '/donkey'), '/')
  assert.equal(normalisePath('/donkeyshop/cli', '/donkey'), '/donkeyshop/cli')
})

test('idFromHash decodes and tolerates empty input', () => {
  assert.equal(idFromHash('#donkey-init'), 'donkey-init')
  assert.equal(idFromHash('#jwt--model-wallet-auth-mode'), 'jwt--model-wallet-auth-mode')
  assert.equal(idFromHash('#%E2%9C%93'), '✓')
  assert.equal(idFromHash(''), '')
  assert.equal(idFromHash('#'), '')
})

const pages = { '/old/a': '/new/a', '/old/b': '/new/b' }
const moved = { '/split': { 'one': '/split/one', 'two': '/split/two' } }

test('a page redirect keeps the fragment', () => {
  assert.equal(resolvePageRedirect('/old/a', '#sec', '', pages, moved), '/new/a#sec')
  assert.equal(resolvePageRedirect('/old/a', '', '', pages, moved), '/new/a')
  assert.equal(resolvePageRedirect('/old/a', '#', '', pages, moved), '/new/a')
})

test('unknown routes resolve to null', () => {
  assert.equal(resolvePageRedirect('/nope', '#x', '', pages, moved), null)
  assert.equal(resolveMovedAnchor('/nope', '#one', '', moved), null)
})

test('a moved anchor wins over the page redirect for the same old path', () => {
  const both = { '/split': '/elsewhere' }
  assert.equal(
    resolvePageRedirect('/split', '#one', '', both, moved),
    '/split/one#one'
  )
  assert.equal(resolvePageRedirect('/split', '#other', '', both, moved), '/elsewhere#other')
})

test('a moved anchor on a split page forwards to the new page with the same id', () => {
  assert.equal(resolveMovedAnchor('/split', '#two', '', moved), '/split/two#two')
  assert.equal(resolveMovedAnchor('/split/', '#two', '', moved), '/split/two#two')
  assert.equal(resolveMovedAnchor('/split', '', '', moved), null)
  assert.equal(resolveMovedAnchor('/split', '#missing', '', moved), null)
})

test('moved anchors honour basePath', () => {
  assert.equal(resolveMovedAnchor('/docs/split', '#one', '/docs', moved), '/split/one#one')
})

test('the shipped registry is internally consistent', () => {
  assert.deepEqual(registryProblems(PAGE_REDIRECTS, MOVED_ANCHORS), [])
})

test('registryProblems flags chains, self-targets, bad shapes and redirected targets', () => {
  const bad = registryProblems(
    { '/a': '/b', '/b': '/c', '/self': '/self', '/x.html': '/y', '/z': '/q#frag' },
    { '/m': { '': '/n', 'id': '/m', 'other': '/a' } }
  )
  const text = bad.join('\n')
  assert.match(text, /chain: \/a -> \/b/)
  assert.match(text, /sends \/self to itself/)
  assert.match(text, /source is not a normalised route: \/x\.html/)
  assert.match(text, /target is not a normalised route: \/z -> \/q#frag/)
  assert.match(text, /empty anchor id/)
  assert.match(text, /points at its own page/)
  assert.match(text, /targets a redirected page: \/a/)
})
