// Syntax check (`node --check`) of every .mjs file the build or CI runs:
// next.config.mjs, scripts/ and lib/. Plain Node, so it behaves the same under
// any shell (no POSIX `for` loop). It is not a style linter.
//
// Run: `npm run lint`. Exit code 1 lists the files that fail to parse.

import { spawnSync } from 'node:child_process'
import { readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const files = ['.', 'scripts', 'lib'].flatMap((d) =>
  readdirSync(join(ROOT, d))
    .filter((f) => f.endsWith('.mjs'))
    .map((f) => join(ROOT, d, f)),
)

let failed = 0
for (const f of files) {
  const r = spawnSync(process.execPath, ['--check', f], { encoding: 'utf8' })
  if (r.status !== 0) {
    failed++
    console.error(r.stderr || `${f}: node --check failed`)
  }
}
if (failed) process.exit(1)
console.log(`lint: ${files.length} .mjs files parse cleanly.`)
