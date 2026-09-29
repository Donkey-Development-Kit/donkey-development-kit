import assert from 'node:assert/strict'
import { test } from 'node:test'

import { CLOUDFLARE_BEACON_SRC, cloudflareBeacon } from './analytics.mjs'

const TOKEN = '0123456789abcdef0123456789abcdef'

test('no token renders no beacon', () => {
  assert.equal(cloudflareBeacon(undefined), null)
  assert.equal(cloudflareBeacon(''), null)
})

test('a valid site token renders the Cloudflare beacon', () => {
  assert.deepEqual(cloudflareBeacon(TOKEN), {
    src: CLOUDFLARE_BEACON_SRC,
    'data-cf-beacon': `{"token":"${TOKEN}"}`,
  })
})

test('malformed tokens are rejected', () => {
  for (const token of [
    TOKEN.toUpperCase(),
    `${TOKEN}0`,
    TOKEN.slice(1),
    `${TOKEN.slice(0, 31)}"`,
    ` ${TOKEN}`,
    `${TOKEN}\n`,
  ]) {
    assert.equal(cloudflareBeacon(token), null, token)
  }
})
