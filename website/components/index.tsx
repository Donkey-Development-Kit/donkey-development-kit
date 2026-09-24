import React from 'react'
import Link from 'next/link'
import NextImage from 'next/image'

/* Presentation components for the docs pages.
 *
 * Nextra's built-in <Cards> renders the description above the title and only
 * pads the title row, which reads as broken once cards carry real prose. These
 * replace it and cover the other repeated patterns (hero, status pills,
 * diagrams). Styles live in ../styles/globals.css. */

// GitHub Pages serves the site under a project sub-path (e.g. /donkey-development-kit).
// next/link auto-prepends basePath, but next/image's unoptimized loader does not
// apply it to /public assets — so <Figure> prefixes root-absolute srcs itself.
// Empty locally and on a custom domain, so it's a no-op there.
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? ''

type Tone = 'live' | 'roadmap' | 'neutral' | 'accent'

export function Badge({
  children,
  tone = 'neutral',
}: {
  children: React.ReactNode
  tone?: Tone
}) {
  return <span className={`af-badge af-badge-${tone}`}>{children}</span>
}

export function Hero({
  eyebrow,
  title,
  tagline,
  media,
  actions,
}: {
  eyebrow?: React.ReactNode
  title: string
  tagline: React.ReactNode
  media?: React.ReactNode
  actions?: { label: string; href: string; primary?: boolean }[]
}) {
  return (
    <header className="af-hero">
      {eyebrow ? <span className="af-hero-eyebrow">{eyebrow}</span> : null}
      <h1 className="af-hero-title">{title}</h1>
      <p className="af-hero-tagline">{tagline}</p>
      {media ? <div className="af-hero-media">{media}</div> : null}
      {actions?.length ? (
        <div className="af-hero-actions">
          {actions.map(action => (
            <Link
              key={action.href}
              href={action.href}
              className={`af-button af-button-${action.primary ? 'primary' : 'secondary'}`}
            >
              {action.label}
            </Link>
          ))}
        </div>
      ) : null}
    </header>
  )
}

export function CardGrid({
  children,
  columns,
}: {
  children: React.ReactNode
  columns?: 2 | 3
}) {
  return (
    <div className="af-card-grid" data-columns={columns}>
      {children}
    </div>
  )
}

export function Card({
  title,
  href,
  icon,
  badge,
  badgeTone = 'neutral',
  children,
}: {
  title: string
  href?: string
  icon?: React.ReactNode
  badge?: string
  badgeTone?: Tone
  children?: React.ReactNode
}) {
  const inner = (
    <>
      <span className="af-card-head">
        {icon ? <span className="af-card-icon">{icon}</span> : null}
        <span className="af-card-title">{title}</span>
      </span>
      {children ? <span className="af-card-body">{children}</span> : null}
      {badge ? (
        <span className="af-card-badges">
          <Badge tone={badgeTone}>{badge}</Badge>
        </span>
      ) : null}
    </>
  )

  if (!href) {
    return <div className="af-card">{inner}</div>
  }
  return (
    <Link href={href} className="af-card">
      {inner}
    </Link>
  )
}

export function Output({
  children,
  label = 'Expected output',
  wrap = false,
}: {
  children: React.ReactNode
  label?: string
  wrap?: boolean
}) {
  return (
    <div className={wrap ? 'af-output af-output-wrap' : 'af-output'}>
      <div className="af-output-label">{label}</div>
      {children}
    </div>
  )
}

export function Figure({
  src,
  alt,
  caption,
  width,
  height,
  priority,
}: {
  src: string
  alt: string
  caption?: React.ReactNode
  width: number
  height: number
  priority?: boolean
}) {
  const resolvedSrc = src.startsWith('/') ? `${BASE_PATH}${src}` : src
  return (
    <figure className="af-figure">
      <NextImage
        src={resolvedSrc}
        alt={alt}
        width={width}
        height={height}
        priority={priority}
        sizes="(max-width: 768px) 100vw, 900px"
      />
      {caption ? <figcaption>{caption}</figcaption> : null}
    </figure>
  )
}

/* ---------------------------------------------------------------- roadmap */

export { RoadmapPhase, RoadmapUpdated } from './roadmap'

/* ------------------------------------------------------------------- team */

export function TeamGrid({ children }: { children: React.ReactNode }) {
  return <div className="af-team-grid">{children}</div>
}

export function TeamMember({
  name,
  role,
  linkedin,
  photo,
}: {
  name: string
  role: 'Creator' | 'Contributor'
  linkedin: string
  photo?: string
}) {
  const initials = name
    .split(/[\s-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map(part => part[0])
    .join('')
  return (
    <a className="af-card af-team-member" href={linkedin} target="_blank" rel="noopener noreferrer">
      {photo ? (
        <img
          className="af-team-avatar af-team-photo"
          src={photo.startsWith('/') ? `${BASE_PATH}${photo}` : photo}
          alt=""
          width={64}
          height={64}
          loading="lazy"
        />
      ) : (
        <span className="af-team-avatar" aria-hidden="true">
          {initials}
        </span>
      )}
      <span className="af-card-title">{name}</span>
      <span className="af-card-badges">
        <Badge tone={role === 'Creator' ? 'accent' : 'neutral'}>{role}</Badge>
        <span className="af-team-link">LinkedIn</span>
      </span>
    </a>
  )
}
