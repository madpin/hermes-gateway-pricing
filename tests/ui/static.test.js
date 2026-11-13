// @vitest-environment node
// Loader-level checks on desktop/plugin.js: what Hermes' runtime loader and admission lint need.
// (Node environment: esbuild refuses to run under jsdom's TextEncoder.)
import { existsSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'

import plugin from '../../desktop/plugin.js'

const repo = process.env.GWP_REPO_ROOT
const source = readFileSync(path.join(repo, 'desktop', 'plugin.js'), 'utf-8')
const esbuild = createRequire(path.join(process.env.GWP_NODE_MODULES, 'noop.js'))('esbuild')
const ALLOWED = ['@hermes/plugin-sdk', 'react', 'react/jsx-runtime']

// Code without comments (esbuild drops them), so prose in comments cannot trip the checks.
const code = esbuild.transformSync(source, { format: 'esm', loader: 'js' }).code

function sdkImports() {
  const block = /import\s*\{([^}]*)\}\s*from\s*['"]@hermes\/plugin-sdk['"]/.exec(source)

  return block[1]
    .split(',')
    .map(name => name.trim().split(/\s+as\s+/)[0].trim())
    .filter(Boolean)
}

/** Value exports of apps/desktop/src/sdk/index.ts (type-only exports skipped). */
export function sdkIndexExports(text) {
  const names = new Set()

  for (const match of text.matchAll(/export\s+(type\s+)?\{([^}]*)\}/g)) {
    if (match[1]) {
      continue
    }

    for (const part of match[2].split(',')) {
      const clean = part.replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '').trim()

      if (!clean || clean.startsWith('type ')) {
        continue
      }

      names.add(clean.split(/\s+as\s+/).pop().trim())
    }
  }

  for (const match of text.matchAll(/export\s+(?:const|let|function|class|async\s+function)\s+([A-Za-z_$][\w$]*)/g)) {
    names.add(match[1])
  }

  for (const match of text.matchAll(/export\s+\*\s+as\s+([A-Za-z_$][\w$]*)/g)) {
    names.add(match[1])
  }

  return names
}

describe('desktop/plugin.js loads uncompiled', () => {
  it('parses as plain JS (no JSX) and has no top-level await', () => {
    expect(() => esbuild.transformSync(source, { format: 'esm', loader: 'js' })).not.toThrow()
    // CommonJS output refuses a top-level await.
    expect(() => esbuild.transformSync(source, { format: 'cjs', loader: 'js' })).not.toThrow()
  })

  it('imports only the three specifiers the loader resolves, with no dynamic import', () => {
    const specifiers = [...code.matchAll(/\bimport\s[^'"]*?['"]([^'"]+)['"]/g)].map(m => m[1])

    expect(new Set(specifiers)).toEqual(new Set(ALLOWED))
    expect(code).not.toMatch(/\bimport\s*\(/)
  })

  it('default-exports {id, name, register} with the agent plugin id', () => {
    expect(Object.keys(plugin).sort()).toEqual(['defaultEnabled', 'id', 'name', 'register'])
    expect(plugin.id).toBe('gateway-pricing')
    expect(plugin.name).toBe('Gateway Pricing')
    expect(typeof plugin.register).toBe('function')
    expect(plugin.defaultEnabled).toBe(false)
  })

  it.each([
    ['window.hermesDesktop', /\bhermesDesktop\b/],
    ['fetch', /(?<![.\w$])fetch\s*\(/],
    ['localStorage / sessionStorage', /\b(?:localStorage|sessionStorage)\b/],
    ['eval', /(?<![.\w$])eval\s*\(/],
    ['Function constructor', /\b(?:new\s+)?Function\s*\(/],
    ['raw timers', /(?<![.\w$])(?:window\.|globalThis\.)?(?:setTimeout|setInterval|requestAnimationFrame)\s*\(/],
    ['DOM listeners (none needed; ctx.addEventListener otherwise)', /\baddEventListener\b/],
    ['document access', /\bdocument\./],
    ['hardcoded colors', /#[0-9a-fA-F]{3,8}\b(?![\w-])|\brgba?\(|\bhsla?\(/]
  ])('does not use %s', (_name, pattern) => {
    expect(code).not.toMatch(pattern)
  })

  it('references only imported or locally defined components in jsx() calls', () => {
    const imported = new Set(sdkImports())
    const local = new Set([...code.matchAll(/function\s+([A-Z][\w$]*)\s*\(/g)].map(m => m[1]))
    const used = new Set([...code.matchAll(/\bjsxs?\(\s*([A-Z][\w$]*)/g)].map(m => m[1]))
    const missing = [...used].filter(name => !imported.has(name) && !local.has(name))

    expect(missing).toEqual([])
    // ...and every SDK import is used (an unused import of a renamed export breaks linking).
    const referenced = name => new RegExp(`\\b${name}\\b`).test(code.slice(code.indexOf('from "@hermes/plugin-sdk"')))
    expect(sdkImports().filter(name => !referenced(name))).toEqual([])
  })

  const index = process.env.HERMES_SDK_INDEX || path.join(repo, '.ref', 'ui', 'sdk-index.ts')
  const haveIndex = existsSync(index)
  const title = haveIndex
    ? 'every name imported from @hermes/plugin-sdk is exported by the real SDK index'
    : 'SDK export check SKIPPED: set HERMES_SDK_INDEX to apps/desktop/src/sdk/index.ts of a Hermes checkout'

  it.skipIf(!haveIndex)(title, () => {
    const exported = sdkIndexExports(readFileSync(index, 'utf-8'))

    expect(exported.size).toBeGreaterThan(50)
    expect(sdkImports().filter(name => !exported.has(name))).toEqual([])
  })
})
