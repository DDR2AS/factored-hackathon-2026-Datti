// Writes dist/build.txt = "<short sha> <ISO date>" after `vite build` (the `postbuild` script of
// package.json), so GET /build.txt on a deployed site says which commit it serves
// (scripts/smoke_api.py reads it). public/build.txt stays "dev" in git: Vite copies it into
// dist/ and this script overwrites that copy only, so a build never dirties the working tree.
// Without git (or outside a repository) the stamp is "dev <date>". Node only, no dependencies.
import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontend = dirname(dirname(fileURLToPath(import.meta.url)))

function shortSha() {
  try {
    const out = execFileSync('git', ['rev-parse', '--short', 'HEAD'], {
      cwd: frontend,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim()
    return /^[0-9a-f]{4,40}$/.test(out) ? out : 'dev'
  } catch {
    return 'dev'
  }
}

const stamp = `${shortSha()} ${new Date().toISOString()}\n`
const dist = join(frontend, 'dist')
mkdirSync(dist, { recursive: true })
writeFileSync(join(dist, 'build.txt'), stamp, 'utf8')
console.log(`build.txt: ${stamp.trim()}`)
