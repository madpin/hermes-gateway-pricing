// The stub may only drift from the real SDK components if this test is skipped: it re-reads the
// props the real components destructure and compares them with the stub's allow-lists.
//
// Sources, first match wins:
//   ListRow / ToggleRow: $HERMES_SETTINGS_PRIMITIVES, $HERMES_DESKTOP_SRC/app/settings/primitives.tsx,
//                        <DESKTOP_NM>/../src/app/settings/primitives.tsx, .ref/ui/settings-primitives.tsx
//   other components:    $HERMES_DESKTOP_SRC/components/ui/*.tsx, <DESKTOP_NM>/../src/components/ui/*.tsx
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'

import { STUB_PROPS } from './sdk-stub.js'

const repo = process.env.GWP_REPO_ROOT
const desktopSrc = [process.env.HERMES_DESKTOP_SRC, process.env.DESKTOP_NM && path.join(process.env.DESKTOP_NM, '..', 'src')].find(
  dir => dir && existsSync(dir)
)
const first = candidates => candidates.find(file => file && existsSync(file)) ?? null

/** Prop names destructured in `export function Name({ ... })` (or a memo'd function). */
export function destructuredProps(text, name) {
  const start = new RegExp(`(?:export\\s+function\\s+${name}|function\\s+${name})\\s*(?:<[^>]*>)?\\s*\\(\\s*\\{`).exec(text)

  if (!start) {
    return null
  }

  let depth = 1
  let i = start.index + start[0].length
  const begin = i

  while (depth > 0 && i < text.length) {
    if (text[i] === '{') {
      depth += 1
    } else if (text[i] === '}') {
      depth -= 1
    }

    i += 1
  }

  const body = text.slice(begin, i - 1)
  // Split on top-level commas only (defaults like `sections = [{ rows: 4 }]` contain commas).
  const parts = []
  let level = 0
  let current = ''

  for (const ch of body) {
    if ('{[('.includes(ch)) {
      level += 1
    } else if ('}])'.includes(ch)) {
      level -= 1
    }

    if (ch === ',' && level === 0) {
      parts.push(current)
      current = ''
    } else {
      current += ch
    }
  }

  parts.push(current)

  return parts
    .map(p => p.trim())
    .filter(p => p && !p.startsWith('...'))
    .map(p => p.split(/[:=]/)[0].trim().replace(/^['"]|['"]$/g, ''))
}

const primitives = first([
  process.env.HERMES_SETTINGS_PRIMITIVES,
  desktopSrc && path.join(desktopSrc, 'app', 'settings', 'primitives.tsx'),
  path.join(repo, '.ref', 'ui', 'settings-primitives.tsx')
])

const CASES = [
  ['ListRow', primitives],
  ['ToggleRow', primitives],
  ['SegmentedControl', desktopSrc && path.join(desktopSrc, 'components', 'ui', 'segmented-control.tsx')],
  ['ConfirmDialog', desktopSrc && path.join(desktopSrc, 'components', 'ui', 'confirm-dialog.tsx')],
  ['EmptyState', desktopSrc && path.join(desktopSrc, 'components', 'ui', 'empty-state.tsx')],
  ['ErrorState', desktopSrc && path.join(desktopSrc, 'components', 'ui', 'error-state.tsx')]
]

describe('sdk-stub matches the real component props', () => {
  for (const [name, file] of CASES) {
    const available = Boolean(file && existsSync(file))
    const title = available
      ? `${name}: stub props equal the real ${path.basename(file)} signature`
      : `${name}: SKIPPED, real source not found (set HERMES_DESKTOP_SRC to apps/desktop/src of a Hermes checkout)`

    it.skipIf(!available)(title, () => {
      const real = destructuredProps(readFileSync(file, 'utf-8'), name)

      expect(real, `no ${name}({ ... }) signature found in ${file}`).not.toBeNull()
      expect([...STUB_PROPS[name].allowed].sort()).toEqual([...real].sort())
    })
  }

  it('parses a known signature shape', () => {
    const text = "export function Row({ title, 'data-tour': tour, wide = false, sections = [{ rows: 4 }], ...rest }: P) {}"

    expect(destructuredProps(text, 'Row')).toEqual(['title', 'data-tour', 'wide', 'sections'])
  })
})
