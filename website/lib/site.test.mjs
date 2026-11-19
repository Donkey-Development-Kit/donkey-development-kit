import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

import { SITE_NAME, SITE_ORIGIN, pageMetadata, pagePath, siteBase } from './site.mjs'

test('siteBase defaults to the custom domain and always ends in a slash', () => {
  assert.equal(siteBase(undefined, undefined), `${SITE_ORIGIN}/`)
  assert.equal(siteBase('https://example.test/', ''), 'https://example.test/')
})

test('siteBase appends the optional sub-path', () => {
  assert.equal(siteBase(undefined, '/ddk'), `${SITE_ORIGIN}/ddk/`)
  assert.equal(siteBase(undefined, 'ddk/'), `${SITE_ORIGIN}/ddk/`)
})

test('pagePath maps a catch-all segment list to a route', () => {
  assert.equal(pagePath(undefined), '/')
  assert.equal(pagePath([]), '/')
  assert.equal(pagePath(['frameworks', 'crewai']), '/frameworks/crewai')
})

test('pageMetadata gives a page its own title, canonical and description', () => {
  const m = pageMetadata({ title: 'Quickstart', description: 'Go.' }, ['quickstart'])
  assert.equal(m.title, 'Quickstart')
  assert.equal(m.alternates.canonical, '/quickstart')
  assert.equal(m.openGraph.title, `Quickstart — ${SITE_NAME}`)
  assert.equal(m.openGraph.url, '/quickstart')
  assert.equal(m.openGraph.description, 'Go.')
  assert.equal(m.openGraph.images.length, 1)
})

test('the home page keeps the bare site name', () => {
  const m = pageMetadata({ title: 'Index' }, undefined)
  assert.deepEqual(m.title, { absolute: SITE_NAME })
  assert.equal(m.openGraph.title, SITE_NAME)
  assert.equal(m.alternates.canonical, '/')
})

test('example pages do not reuse the framework page title', () => {
  const fw = pageMetadata({ title: 'CrewAI' }, ['frameworks', 'crewai'])
  const ex = pageMetadata({ title: 'CrewAI' }, ['examples', 'crewai'])
  assert.notEqual(fw.openGraph.title, ex.openGraph.title)
  assert.notEqual(fw.title, ex.title)
})

test('roadmap page, generator and snapshot include the same milestones in order', () => {
  const page = readFileSync(new URL('../content/roadmap.mdx', import.meta.url), 'utf8')
  const generator = readFileSync(new URL('../scripts/generate-roadmap.mjs', import.meta.url), 'utf8')
  const snapshot = JSON.parse(readFileSync(new URL('../data/roadmap.json', import.meta.url), 'utf8'))
  const prefixes = [...page.matchAll(/<RoadmapPhase milestone="([^"]+)"/g)].map(match => match[1])
  const tracked = [...generator.match(/const TRACKED = \[([^\]]+)\]/)[1].matchAll(/'([^']+)'/g)].map(match => match[1])
  assert.ok(prefixes.includes('Phase 2.2'))
  assert.deepEqual(tracked, prefixes)
  assert.equal(snapshot.milestones.length, prefixes.length)
  for (const [index, milestone] of snapshot.milestones.entries()) {
    const prefix = prefixes[index]
    assert.ok(milestone.title === prefix || milestone.title.startsWith(`${prefix} `))
    assert.equal(milestone.open, milestone.issues.filter(issue => issue.state === 'open').length)
    assert.equal(milestone.closed, milestone.issues.filter(issue => issue.state === 'closed').length)
    const numbers = milestone.issues.map(issue => issue.number)
    assert.deepEqual(numbers, [...new Set(numbers)].sort((a, b) => a - b))
  }
})
