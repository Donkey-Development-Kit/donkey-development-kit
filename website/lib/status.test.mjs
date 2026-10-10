import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { STATUSES, statusOf } from './status.mjs'

const CONTENT = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'content')

function frontmatter(src) {
  const m = src.match(/^---\n([\s\S]*?)\n---\n/)
  const out = {}
  for (const line of m ? m[1].split('\n') : []) {
    const kv = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/)
    if (kv) out[kv[1]] = kv[2].replace(/^["']|["']$/g, '').trim()
  }
  return out
}

async function pages() {
  const entries = await readdir(CONTENT, { recursive: true })
  return entries.filter(e => e.endsWith('.mdx')).sort()
}

test('the vocabulary is exactly live, offline-verified, roadmap', () => {
  assert.deepEqual(Object.keys(STATUSES), ['live', 'offline-verified', 'roadmap'])
})

test('statusOf rejects a missing or unknown status', () => {
  assert.throws(() => statusOf({}, 'x.mdx'), /x\.mdx: frontmatter status must be one of/)
  assert.throws(() => statusOf({ status: 'beta' }), /got "beta"/)
  assert.equal(statusOf({ status: 'roadmap' }).label, 'Roadmap')
  assert.equal(statusOf({ status: 'live', statusBadge: 'false' }).badge, false)
  assert.equal(statusOf({ status: 'live' }).badge, true)
})

test('every page declares a valid status in its frontmatter', async () => {
  for (const page of await pages()) {
    const fm = frontmatter(await readFile(join(CONTENT, page), 'utf8'))
    assert.doesNotThrow(() => statusOf(fm, page), page)
  }
})

// The page-level badge comes from frontmatter, so a hand-written one would
// show twice (or contradict the status). Inline badges in tables, cards and
// legends further down are fine; this checks the lines right under the H1.
test('no page hand-writes its page-level status badge', async () => {
  for (const page of await pages()) {
    const lines = (await readFile(join(CONTENT, page), 'utf8')).split('\n')
    const h1 = lines.findIndex(l => l.startsWith('# '))
    if (h1 < 0) continue
    const next = lines.slice(h1 + 1).find(l => l.trim() !== '') ?? ''
    assert.doesNotMatch(next, /^<Badge\b/, `${page}: drop the badge under the H1; set status: in frontmatter`)
  }
})
