// Copies ../fixtures/*.json and *.jsonl into public/fixtures/ (gitignored) so fixture mode runs with no backend.
import { copyFileSync, mkdirSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = join(here, '..', '..', 'fixtures')
const dest = join(here, '..', 'public', 'fixtures')
mkdirSync(dest, { recursive: true })
const files = readdirSync(src).filter((f) => f.endsWith('.json') || f.endsWith('.jsonl'))
for (const f of files) copyFileSync(join(src, f), join(dest, f))
console.log(`copied ${files.length} fixture files to public/fixtures/`)
