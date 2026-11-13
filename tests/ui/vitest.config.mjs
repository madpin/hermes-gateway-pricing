// Vitest config for the desktop settings UI tests.
//
// desktop/plugin.js lives outside this folder, so its `react` imports cannot find
// tests/ui/node_modules by walking up. Every runtime package is therefore aliased to one
// node_modules directory: $DESKTOP_NM when set (a Hermes desktop checkout's node_modules,
// used read-only), else ./node_modules (what `npm install` in this folder creates; CI).
// `@hermes/plugin-sdk` always resolves to the local stub.
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const repo = path.resolve(here, '../..')
const modules = process.env.DESKTOP_NM || path.join(here, 'node_modules')
const pkg = name => path.join(modules, name)

export default {
  root: here,
  resolve: {
    alias: [
      { find: '@hermes/plugin-sdk', replacement: path.join(here, 'sdk-stub.js') },
      { find: /^react-dom(\/.*)?$/, replacement: `${pkg('react-dom')}$1` },
      { find: /^react(\/.*)?$/, replacement: `${pkg('react')}$1` },
      { find: /^@testing-library\/react$/, replacement: pkg('@testing-library/react') },
      { find: /^@testing-library\/dom$/, replacement: pkg('@testing-library/dom') }
    ]
  },
  server: { fs: { allow: [repo, modules] } },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['**/*.test.js'],
    exclude: ['node_modules/**'],
    env: { GWP_NODE_MODULES: modules, GWP_REPO_ROOT: repo }
  }
}
