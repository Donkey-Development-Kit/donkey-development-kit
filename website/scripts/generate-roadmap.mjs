// Snapshot the delivery milestones and their issues from the GitHub API into
// data/roadmap.json, which the Roadmap page renders.
//
// The snapshot is committed so that `next build` never depends on the network:
// when the API is unreachable or rate-limited, the previous snapshot is kept
// and the build carries on. The Pages workflow runs this before every build
// (and on a daily schedule), so the published page tracks the milestones.
//
// Set GITHUB_TOKEN to lift the unauthenticated rate limit (60 requests/hour).
//
// Run: `npm run generate:roadmap` (also runs automatically via `prebuild`).

import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT = join(resolve(HERE, '..'), 'data', 'roadmap.json')

const REPO = 'Donkey-Development-Kit/donkey-development-kit'
const API = `https://api.github.com/repos/${REPO}`

// Milestones shown on the Roadmap page, in display order. Matched by title
// prefix so a version bump in the title does not drop a phase.
const TRACKED = ['Phase 1', 'Phase 2', 'Phase 3', 'Phase 4', 'Phase 5', 'Verification', 'Upstream gaps']

async function gh(path) {
  const headers = { accept: 'application/vnd.github+json', 'user-agent': 'ddk-docs-roadmap' }
  if (process.env.GITHUB_TOKEN) headers.authorization = `Bearer ${process.env.GITHUB_TOKEN}`
  const res = await fetch(`${API}${path}`, { headers, signal: AbortSignal.timeout(15000) })
  if (!res.ok) throw new Error(`GET ${path} → ${res.status} ${res.statusText}`)
  return res.json()
}

async function issuesFor(milestoneNumber) {
  const issues = []
  for (let page = 1; ; page++) {
    const batch = await gh(`/issues?milestone=${milestoneNumber}&state=all&per_page=100&page=${page}`)
    issues.push(...batch.filter((i) => !i.pull_request))
    if (batch.length < 100) break
  }
  return issues
    .map((i) => ({
      number: i.number,
      title: i.title,
      state: i.state,
      url: i.html_url,
      epic: i.labels.some((l) => l.name === 'epic'),
    }))
    .sort((a, b) => a.number - b.number)
}

async function main() {
  const milestones = await gh('/milestones?state=all&per_page=100')
  const tracked = []
  for (const prefix of TRACKED) {
    const m = milestones.find((x) => x.title.startsWith(prefix))
    if (!m) continue
    const issues = await issuesFor(m.number)
    tracked.push({
      number: m.number,
      title: m.title,
      state: m.state,
      url: m.html_url,
      open: issues.filter((i) => i.state === 'open').length,
      closed: issues.filter((i) => i.state === 'closed').length,
      issues,
    })
  }
  const snapshot = { repo: REPO, generatedAt: new Date().toISOString(), milestones: tracked }
  await mkdir(dirname(OUT), { recursive: true })
  await writeFile(OUT, `${JSON.stringify(snapshot, null, 2)}\n`)
  const total = tracked.reduce((n, m) => n + m.issues.length, 0)
  console.log(`Wrote data/roadmap.json: ${tracked.length} milestones, ${total} issues.`)
}

main().catch((err) => {
  console.warn(`Roadmap snapshot not refreshed (${err.message}); keeping the committed data/roadmap.json.`)
})
