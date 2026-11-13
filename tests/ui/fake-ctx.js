// Fake PluginContext + fake `plugins.manage` + render helpers for the desktop/plugin.js tests.
//
// - ctx.rest is backed by fixtures generated from the real backend (fixtures/*.json), records
//   every call, and lets a test swap a route for a failure, a slow (deferred) answer, etc.
// - ctx.setTimeout / setInterval run on a manual clock: ctx.advance(ms) fires what is due.
//   (Not vi.useFakeTimers: Testing Library's own waits keep using real timers.)
// - ctx.onEvent listeners can be fired with ctx.emit(type, payload).
// - ctx.dispose() runs what the host runs on unload: onDispose callbacks, timers, listeners.
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { act, render } from '@testing-library/react'
import { createElement } from 'react'

import { host, resetHost } from './sdk-stub.js'

const here = path.dirname(fileURLToPath(import.meta.url))

export function fixture(name) {
  return JSON.parse(readFileSync(path.join(here, 'fixtures', `${name}.json`), 'utf-8'))
}

/**
 * What ctx.rest rejects with for an HTTP error, as the renderer sees it: Electron's main process
 * throws Error("<status>: <body>") with .statusCode (api-transport.ts httpStatusError), and the IPC
 * hop keeps only the message, behind its own "Error invoking remote method" prefix. This is the
 * least structured form, so the UI is tested against it.
 */
export function httpError(status, detail) {
  return new Error(`Error invoking remote method 'hermes:api': Error: ${status}: ${JSON.stringify({ detail })}`)
}

export function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })

  return { promise, resolve, reject }
}

export function defaultRoutes() {
  return {
    'GET /status': () => fixture('status'),
    'GET /detected': () => fixture('detected'),
    'POST /preview': () => fixture('preview-price-as'),
    'POST /backfill': ({ body }) => {
      if (body?.dry_run === false) {
        if (body.confirm !== true) {
          throw httpError(409, fixture('error-409').detail)
        }

        return fixture('backfill-applied')
      }

      return fixture('backfill-dry-run')
    }
  }
}

export function makeCtx({ routes = {}, settingsPage = true, i18n = true } = {}) {
  const timers = new Map()
  const listeners = new Map()
  const disposers = []
  const store = new Map()
  let timerId = 0

  const ctx = {
    source: 'plugin:gateway-pricing',
    pages: [],
    contributions: [],
    restCalls: [],
    i18nBundles: [],
    routes: { ...defaultRoutes(), ...routes },
    now: 0,
    disposed: false,

    register(contribution) {
      ctx.contributions.push(contribution)

      return () => {
        ctx.contributions = ctx.contributions.filter(c => c !== contribution)
      }
    },

    rest(requestPath, opts = {}) {
      const method = (opts.method ?? 'GET').toUpperCase()
      const [route, query = ''] = requestPath.split('?')
      const call = { method, path: requestPath, route, query, body: opts.body, timeoutMs: opts.timeoutMs }
      ctx.restCalls.push(call)
      const handler = ctx.routes[`${method} ${route}`]

      if (!handler) {
        return Promise.reject(httpError(404, 'Not Found'))
      }

      try {
        return Promise.resolve(handler(call))
      } catch (error) {
        return Promise.reject(error)
      }
    },

    onEvent(type, listener) {
      if (!listeners.has(type)) {
        listeners.set(type, new Set())
      }

      listeners.get(type).add(listener)

      return () => listeners.get(type)?.delete(listener)
    },

    emit(type, payload = {}) {
      for (const listener of listeners.get(type) ?? []) {
        listener({ type, payload, session_id: '' })
      }
    },

    listenerCount(type) {
      return listeners.get(type)?.size ?? 0
    },

    onDispose(fn) {
      disposers.push(fn)
    },

    setTimeout(fn, ms) {
      const id = ++timerId
      timers.set(id, { at: ctx.now + ms, fn })

      return () => timers.delete(id)
    },

    setInterval(fn, ms) {
      const id = ++timerId
      timers.set(id, { at: ctx.now + ms, fn, every: ms })

      return () => timers.delete(id)
    },

    addEventListener(target, type, listener, options) {
      target.addEventListener(type, listener, options)
      const off = () => target.removeEventListener(type, listener, options)
      disposers.push(off)

      return off
    },

    pendingTimers() {
      return timers.size
    },

    /** Move the manual clock forward and fire every timer that came due, in order. */
    advance(ms) {
      const until = ctx.now + ms

      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0]

        if (!due) {
          break
        }

        const [id, timer] = due
        ctx.now = timer.at

        if (timer.every) {
          timer.at += timer.every
        } else {
          timers.delete(id)
        }

        timer.fn()
      }

      ctx.now = until
    },

    dispose() {
      ctx.disposed = true
      disposers.splice(0).forEach(fn => fn())
      timers.clear()
      listeners.clear()
    },

    storage: {
      get: (key, fallback) => (store.has(key) ? store.get(key) : fallback),
      set: (key, value) => store.set(key, value),
      remove: key => store.delete(key)
    }
  }

  if (settingsPage) {
    ctx.registerSettingsPage = page => {
      ctx.pages.push(page)

      return () => {
        ctx.pages = ctx.pages.filter(p => p !== page)
      }
    }
  }

  if (i18n) {
    ctx.i18n = { register: bundles => ctx.i18nBundles.push(bundles) }
  }

  return ctx
}

/** A fake gateway behind host.request('plugins.manage', …), holding one profile's settings. */
export function makeGateway(values = fixture('settings')) {
  const gateway = {
    values: JSON.parse(JSON.stringify(values)),
    calls: [],
    /** Replace to make `settings` fail: (params) => throws or returns {ok:false, error}. */
    onSettings: null,
    onList: null,

    async request(method, params, timeoutMs) {
      gateway.calls.push({ method, params: JSON.parse(JSON.stringify(params)), timeoutMs })

      if (method !== 'plugins.manage') {
        throw new Error(`unexpected RPC ${method}`)
      }

      if (params.action === 'list') {
        if (gateway.onList) {
          return gateway.onList(params)
        }

        return { plugins: [gateway.row()] }
      }

      if (params.action === 'settings') {
        if (gateway.onSettings) {
          const answer = await gateway.onSettings(params)

          if (answer) {
            return answer
          }
        }

        gateway.values = { ...gateway.values, ...JSON.parse(JSON.stringify(params.values)) }

        return { ok: true, name: 'gateway-pricing', written: Object.keys(params.values), plugin: gateway.row() }
      }

      throw new Error(`unexpected action ${params.action}`)
    },

    // settings_schema as Hermes builds it (fixtures/settings-fields.json comes from the real
    // hermes_cli.plugins_settings.plugin_settings_fields): value = saved value, else the default.
    row() {
      return {
        key: 'gateway-pricing',
        name: 'gateway-pricing',
        version: '0.2.0',
        status: 'enabled',
        has_desktop_half: true,
        settings_schema: fixture('settings-fields').map(field => ({
          ...field,
          value: field.key in gateway.values ? JSON.parse(JSON.stringify(gateway.values[field.key])) : field.default
        }))
      }
    },

    settingsCalls() {
      return gateway.calls.filter(c => c.params.action === 'settings')
    }
  }

  return gateway
}

/** Let pending promises (fake REST answers, state updates) settle inside act(). */
export async function flush(rounds = 3) {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {
      await new Promise(resolve => setTimeout(resolve, 0))
    })
  }
}

export async function advance(ctx, ms) {
  await act(async () => {
    ctx.advance(ms)
  })
  await flush()
}

/**
 * Register the plugin against a fresh ctx + gateway and return helpers.
 * `profile`: what host.state.profile reports ('' = the backend's launch profile).
 */
export async function setup(plugin, { profile = '', values, ctxOptions } = {}) {
  resetHost()
  const gateway = makeGateway(values)
  host.request = gateway.request
  host.state.profile._set(profile)
  const ctx = makeCtx(ctxOptions)
  plugin.register(ctx)

  const pageNode = id => {
    const root = ctx.pages[0] ?? ctx.contributions.find(c => c.area === 'settings.plugins')
    const children = root?.children ?? root?.data?.children ?? []

    return id === 'overview' ? root : children.find(c => c.id === id)
  }

  const show = async id => {
    const view = render(createElement(() => pageNode(id).render()))
    await flush()

    return view
  }

  return { ctx, gateway, host, pageNode, show }
}
