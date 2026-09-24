'use client'

import React, { useEffect, useState } from 'react'
import snapshot from '../data/roadmap.json'

/* Roadmap milestones, live from GitHub.
 *
 * The server render (and any visitor without JavaScript) shows the build-time
 * snapshot in data/roadmap.json, written by scripts/generate-roadmap.mjs. After
 * hydration the page refetches the milestones and their issues from the public
 * GitHub REST API and re-renders. No token is ever used here: the site is
 * public, so anything shipped to the browser is readable by every visitor.
 * The unauthenticated limit is 60 requests/hour per IP, and visitors behind a
 * corporate NAT share one IP; one load costs four requests, so the result is
 * cached in localStorage across tabs. Any failure, including a rate-limit 403,
 * keeps the snapshot. */

type RoadmapIssue = {
  number: number
  title: string
  state: string
  url: string
  epic: boolean
}

type Milestone = {
  number: number
  title: string
  state: string
  url: string
  open: number
  closed: number
  issues: RoadmapIssue[]
}

type Roadmap = { generatedAt: string; milestones: Milestone[]; live?: boolean }

const API = `https://api.github.com/repos/${snapshot.repo}`
const CACHE_KEY = 'ddk-roadmap-live-v1'
const CACHE_TTL_MS = 30 * 60 * 1000

type GhLabel = { name: string }
type GhIssue = {
  number: number
  title: string
  state: string
  html_url: string
  labels: GhLabel[]
  pull_request?: unknown
  milestone: { number: number } | null
}
type GhMilestone = { number: number; title: string; state: string; html_url: string }

async function gh<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`, { headers: { accept: 'application/vnd.github+json' } })
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`)
  return res.json() as Promise<T>
}

async function fetchLive(): Promise<Roadmap> {
  const milestones = await gh<GhMilestone[]>('/milestones?state=all&per_page=100')
  const issues: GhIssue[] = []
  for (let page = 1; page <= 20; page++) {
    const batch = await gh<GhIssue[]>(`/issues?milestone=*&state=all&per_page=100&page=${page}`)
    issues.push(...batch.filter(i => !i.pull_request))
    if (batch.length < 100) break
  }
  return {
    generatedAt: new Date().toISOString(),
    live: true,
    milestones: milestones.map(m => {
      const own = issues
        .filter(i => i.milestone?.number === m.number)
        .map(i => ({
          number: i.number,
          title: i.title,
          state: i.state,
          url: i.html_url,
          epic: i.labels.some(l => l.name === 'epic'),
        }))
        .sort((a, b) => a.number - b.number)
      return {
        number: m.number,
        title: m.title,
        state: m.state,
        url: m.html_url,
        open: own.filter(i => i.state === 'open').length,
        closed: own.filter(i => i.state !== 'open').length,
        issues: own,
      }
    }),
  }
}

function readCache(): Roadmap | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (!raw) return null
    const { at, data } = JSON.parse(raw) as { at: number; data: Roadmap }
    return Date.now() - at < CACHE_TTL_MS ? data : null
  } catch {
    return null
  }
}

function writeCache(data: Roadmap) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({ at: Date.now(), data }))
  } catch {
    // Storage full or disabled: the live data still renders, just uncached.
  }
}

// One request per page load, shared by every RoadmapPhase on the page.
let inflight: Promise<Roadmap | null> | null = null

function loadLive(): Promise<Roadmap | null> {
  if (!inflight) {
    const cached = readCache()
    inflight = cached
      ? Promise.resolve(cached)
      : fetchLive()
          .then(data => {
            writeCache(data)
            return data
          })
          .catch(() => null)
  }
  return inflight
}

function useRoadmap(): Roadmap {
  const [data, setData] = useState<Roadmap>(snapshot as Roadmap)
  useEffect(() => {
    let active = true
    loadLive().then(live => {
      if (active && live) setData(live)
    })
    return () => {
      active = false
    }
  }, [])
  return data
}

type Tone = 'live' | 'roadmap' | 'accent'

function IssueList({ issues }: { issues: RoadmapIssue[] }) {
  return (
    <ul className="af-issue-list">
      {issues.map(issue => (
        <li key={issue.number} data-state={issue.state}>
          <span className="af-issue-state" aria-label={issue.state === 'open' ? 'Open' : 'Done'}>
            {issue.state === 'open' ? '○' : '●'}
          </span>
          <a href={issue.url} target="_blank" rel="noopener noreferrer">
            #{issue.number}
          </a>{' '}
          {issue.title}
        </li>
      ))}
    </ul>
  )
}

// One milestone: a status pill, a progress bar, and its issues split into open and done.
export function RoadmapPhase({
  milestone,
  children,
}: {
  milestone: string
  children?: React.ReactNode
}) {
  const roadmap = useRoadmap()
  const m = roadmap.milestones.find(x => x.title.startsWith(milestone))
  if (!m) return null
  const total = m.open + m.closed
  const pct = total ? Math.round((m.closed / total) * 100) : 0
  const status: { label: string; tone: Tone } =
    m.open === 0 && total > 0
      ? { label: 'Complete', tone: 'live' }
      : m.closed > 0
        ? { label: 'In progress', tone: 'accent' }
        : { label: 'Planned', tone: 'roadmap' }
  const open = m.issues.filter(i => i.state === 'open')
  const done = m.issues.filter(i => i.state !== 'open')
  return (
    <section className="af-phase">
      <div className="af-phase-head">
        <a className="af-phase-title" href={m.url} target="_blank" rel="noopener noreferrer">
          {m.title}
        </a>
        <span className={`af-badge af-badge-${status.tone}`}>{status.label}</span>
      </div>
      {children ? <div className="af-phase-body">{children}</div> : null}
      <div className="af-progress" role="img" aria-label={`${pct}% complete`}>
        <span style={{ width: `${pct}%` }} />
      </div>
      <p className="af-phase-stats">
        {pct}% complete · {m.closed} done · {m.open} open
      </p>
      {open.length ? (
        <details className="af-phase-issues">
          <summary>Open issues ({open.length})</summary>
          <IssueList issues={open} />
        </details>
      ) : null}
      {done.length ? (
        <details className="af-phase-issues">
          <summary>Done ({done.length})</summary>
          <IssueList issues={done} />
        </details>
      ) : null}
    </section>
  )
}

export function RoadmapUpdated() {
  const roadmap = useRoadmap()
  if (roadmap.live) {
    return <span className="af-roadmap-updated">Status is live from the GitHub milestones.</span>
  }
  const date = new Date(roadmap.generatedAt)
  return (
    <span className="af-roadmap-updated">
      Status synced from GitHub milestones on{' '}
      {date.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })}.
    </span>
  )
}
