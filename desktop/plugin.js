/**
 * gateway-pricing: desktop settings page (Settings -> Plugins -> Gateway Pricing).
 *
 * Loaded UNCOMPILED by Hermes Desktop: plain ESM, jsx()/jsxs() calls instead of JSX, and only the
 * three specifiers the plugin loader resolves (@hermes/plugin-sdk, react, react/jsx-runtime).
 *
 * Data flow
 *   - Rules, aliases and the master switch are the agent plugin's settings. They are read with
 *     host.request('plugins.manage', {action: 'list'}) (settings_schema[].value) and written with
 *     host.request('plugins.manage', {action: 'settings', key, values}), Hermes' own writer.
 *   - Status, detection, preview and backfill come from the plugin's backend (dashboard/plugin_api.py)
 *     through ctx.rest, which Hermes scopes to the active profile (host.state.profile). The
 *     plugins.manage calls carry that same profile, so reads and writes hit one profile.
 *   - Edits live in a draft until Save. Hermes broadcasts plugin.gateway-pricing.settings.changed
 *     after every save; the page re-reads and reloads, or offers "Reload / Keep mine" when dirty.
 *
 * Sections: strings - helpers - rule model and validation - state and backend - shared UI -
 * rule editor - pages - registration.
 */

import {
  Badge,
  Button,
  ConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  ErrorState,
  host,
  Input,
  ListRow,
  SegmentedControl,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  SETTINGS_PLUGINS_AREA,
  StatusDot,
  Switch,
  ToggleRow
} from '@hermes/plugin-sdk'
import { useEffect, useId, useRef, useState, useSyncExternalStore } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const PLUGIN_ID = 'gateway-pricing'
const SETTINGS_EVENT = `plugin.${PLUGIN_ID}.settings.changed`
const REQUEST_TIMEOUT_MS = 15000
const APPLY_TIMEOUT_MS = 120000
const PREVIEW_DEBOUNCE_MS = 300
const DETECT_DAYS = 30

// ═════════════════════════════════════════════════════════════════════════════════════════════
// Strings (English). One table, read through t(); registered with ctx.i18n when the host has it.
// ═════════════════════════════════════════════════════════════════════════════════════════════

const STRINGS = {
  'page.title': 'Gateway Pricing',
  'page.overview': 'Overview',
  'page.rules': 'Rules',
  'page.aliases': 'Aliases',
  'page.backfill': 'Backfill',

  'common.retry': 'Retry',
  'common.cancel': 'Cancel',
  'common.loading': 'Loading…',
  'common.save': 'Save',
  'common.saving': 'Saving…',
  'common.discard': 'Discard',
  'common.done': 'Done',
  'common.remove': 'Remove',

  'status.active': 'Active',
  'status.inactive': 'Inactive',
  'status.title': 'Pricing status',
  'status.meta': (profile, plugin, hermes) =>
    `Profile ${profile || 'default'} · plugin ${plugin || '?'} · Hermes ${hermes || 'unknown'}`,
  'status.rules': (valid, total) => `${valid} of ${total} rules valid`,
  'status.inactiveNext':
    'Check that gateway-pricing is listed in plugins.enabled for this profile, then restart the gateway.',
  'status.warnings': 'Warnings',
  'overview.enabled': 'Apply pricing rules',
  'overview.enabledHint': 'Master switch. Off leaves every Hermes price untouched.',
  'overview.unpriced': 'Not priced yet',
  'overview.unpricedHint': n => `Endpoints and models from the last ${n} days that no rule prices.`,
  'overview.emptyTitle': 'Nothing unpriced',
  'overview.emptyBody': n => `Every endpoint used in the last ${n} days is priced by Hermes or by a rule.`,
  'overview.matchedHint': n =>
    `${n} recorded endpoint${n === 1 ? '' : 's'} already match a rule. Use Backfill to price their older sessions.`,
  'overview.usage': (host, sessions, tokens) =>
    `${host} · ${sessions} session${sessions === 1 ? '' : 's'} · ${tokens} tokens`,
  'overview.suggestion': (what, confidence) => `Suggestion: ${what} (${confidence} confidence)`,
  'overview.noSuggestion': 'No suggestion: the vendor is unknown. Add explicit prices or mark it free.',
  'overview.addRule': 'Add rule',
  'overview.addRuleFor': model => `Add rule for ${model}`,
  'overview.detectError': 'Could not list unpriced endpoints',

  'rules.firstMatch': 'Rules are checked from top to bottom; the first rule that matches wins.',
  'rules.emptyTitle': 'No rules yet',
  'rules.emptyBody': 'Add a rule, or start from a suggestion on the Overview page.',
  'rules.add': 'Add rule',
  'rules.label': n => `Rule ${n}`,
  'rules.allModels': 'all models',
  'rules.enable': label => `Enable ${label}`,
  'rules.edit': label => `Edit ${label}`,
  'rules.duplicate': label => `Duplicate ${label}`,
  'rules.delete': label => `Delete ${label}`,
  'rules.up': label => `Move ${label} up`,
  'rules.down': label => `Move ${label} down`,
  'rules.editShort': 'Edit',
  'rules.duplicateShort': 'Duplicate',
  'rules.deleteShort': 'Delete',
  'rules.override': 'override',
  'rules.invalid': 'invalid',
  'rules.deleteTitle': label => `Delete ${label}?`,
  'rules.deleteBody': 'The rule is removed from your draft. Nothing changes until you save.',
  'rules.otherWarnings': 'Settings warnings',
  'mode.auto': 'bills like the detected vendor',
  'mode.priceAs': target => `bills like ${target}`,
  'mode.prices': (input, output) => `$${input} / $${output} per 1M`,
  'mode.cache': (read, write) => `cache ${read ? `read $${read}` : ''}${read && write ? ', ' : ''}${write ? `write $${write}` : ''}`,
  'mode.free': 'free',
  'mode.multiplier': m => `x${m}`,

  'editor.add': 'Add rule',
  'editor.edit': 'Edit rule',
  'editor.description': 'Changes go into your draft. Save applies them.',
  'editor.baseUrl': 'Base URL',
  'editor.baseUrlHint': 'host[:port][/path], for example gateway.example.com/openai or 127.0.0.1:9001',
  'editor.models': 'Models (optional)',
  'editor.modelsHint': 'Comma-separated globs, for example claude-*, gpt-5*. Empty matches every model.',
  'editor.mode': 'Pricing',
  'editor.modePriceAs': 'Bill like a vendor',
  'editor.modePrices': 'Explicit prices',
  'editor.modeFree': 'Free',
  'editor.vendor': 'Vendor',
  'editor.vendorAuto': 'auto (from the model id)',
  'editor.vendorModel': 'Vendor model (optional)',
  'editor.vendorModelHint': 'Price every matching call as this model, for example claude-sonnet-4-5.',
  'editor.price.input': 'Input',
  'editor.price.output': 'Output',
  'editor.price.cache_read': 'Cache read (optional)',
  'editor.price.cache_write': 'Cache write (optional)',
  'editor.pricesHint': 'USD per 1M tokens.',
  'editor.freeHint': 'Calls are recorded as included ($0).',
  'editor.multiplier': 'Multiplier (optional)',
  'editor.multiplierHint': 'Scales the price, for example 1.1 for a 10% gateway fee.',
  'editor.override': 'Override Hermes prices',
  'editor.overrideHint': 'Also apply to routes Hermes already prices.',
  'editor.id': 'Rule id (optional)',
  'editor.idHint': 'Shown in previews, backfill results and warnings.',
  'editor.enabled': 'Rule enabled',
  'editor.fixErrors': 'Fix the highlighted fields first.',

  'preview.title': 'Live preview',
  'preview.model': 'Model to test',
  'preview.baseUrl': 'Base URL to test',
  'preview.needInput': 'Enter a model and a base URL to preview.',
  'preview.loading': 'Pricing…',
  'preview.matched': 'Matched rule',
  'preview.noMatch': 'no rule',
  'preview.thisRule': '(this rule)',
  'preview.otherRule': '(an earlier rule wins)',
  'preview.status': 'Status',
  'preview.source': 'Source',
  'preview.amount': 'Cost of 1M in + 1M out',
  'preview.route': 'Priced as',
  'preview.rates': 'Per 1M tokens',
  'preview.reason': 'Why',
  'preview.failed': 'Preview failed',

  'aliases.intro': 'Rewrite a model id before pricing, for example chat-latest -> gpt-5.1. Aliases never chain.',
  'aliases.from': n => `Alias ${n}: model id`,
  'aliases.to': n => `Alias ${n}: price as`,
  'aliases.add': 'Add alias',
  'aliases.remove': n => `Remove alias ${n}`,
  'aliases.emptyTitle': 'No aliases',
  'aliases.emptyBody': 'Add one when a gateway uses its own name for a vendor model.',
  'aliases.duplicate': key => `"${key}" is listed more than once.`,
  'aliases.incomplete': 'Fill in both sides, or remove the row.',

  'backfill.intro':
    'Recompute the cost of past sessions that Hermes stored as unknown, using the SAVED rules. Rows Hermes priced from a provider invoice are never touched.',
  'backfill.days': 'Sessions from',
  'backfill.days.7': 'Last 7 days',
  'backfill.days.30': 'Last 30 days',
  'backfill.days.90': 'Last 90 days',
  'backfill.days.365': 'Last year',
  'backfill.days.all': 'All time',
  'backfill.preview': 'Preview changes',
  'backfill.apply': 'Apply…',
  'backfill.dirty': 'Save or discard your changes first: backfill uses the saved rules.',
  'backfill.needPreview': 'Preview the changes first.',
  'backfill.nothing': 'Nothing to change.',
  'backfill.summary': (rows, sessions) =>
    `${rows} usage row${rows === 1 ? '' : 's'} in ${sessions} session${sessions === 1 ? '' : 's'} would change.`,
  'backfill.delta': 'Cost added',
  'backfill.considered': 'Rows considered',
  'backfill.table': 'Changes by endpoint',
  'backfill.col.endpoint': 'Endpoint',
  'backfill.col.rule': 'Rule',
  'backfill.col.models': 'Models',
  'backfill.col.rows': 'Rows',
  'backfill.col.delta': 'Cost added',
  'backfill.confirmTitle': 'Apply backfill?',
  'backfill.confirmBody': (rows, sessions) =>
    `This writes ${rows} usage rows in ${sessions} sessions of state.db. A backup of state.db is made first, and everything runs in one transaction.`,
  'backfill.confirm': 'Back up and apply',
  'backfill.applied': (rows, sessions) => `Updated ${rows} rows in ${sessions} sessions.`,
  'backfill.backup': path => `Backup: ${path}`,
  'backfill.noBackup': 'Nothing was written, so no backup was needed.',

  'dirty.label': 'Unsaved changes',
  'dirty.message': 'You have unsaved changes to the pricing rules.',
  'dirty.aliasErrors': 'Fix the alias errors before saving.',
  'dirty.saveFailed': message => `Hermes did not save the changes: ${message}`,
  'dirty.saveHint':
    'Your draft is kept. If Hermes is a managed install, its settings may be read-only; edit config.yaml or ask the administrator.',
  'remote.message': 'These settings were changed elsewhere (another window, the CLI or config.yaml).',
  'remote.reload': 'Reload',
  'remote.keep': 'Keep mine',
  'profile.changed': (from, to) =>
    `The active profile switched to ${to || 'default'}. Your unsaved edits belong to ${from || 'default'}; Save writes them there.`,

  'error.notMounted.title': 'The gateway-pricing API is not available',
  'error.notMounted.body':
    'Add gateway-pricing to plugins.enabled for this profile (Capabilities -> Plugins), restart the gateway so its API mounts, then retry.',
  'error.unavailable.title': 'Gateway pricing is not active in the gateway',
  'error.unavailable.body': detail =>
    `${detail ? `${detail}. ` : ''}Restart the gateway after enabling the plugin, then retry.`,
  'error.timeout.title': 'Hermes did not answer in time',
  'error.timeout.body': 'Check that the gateway is running and not busy, then retry.',
  'error.offline.title': 'Not connected to Hermes',
  'error.offline.body': 'Wait for the gateway connection, then retry.',
  'error.noDb.title': 'No sessions recorded yet',
  'error.noDb.body': 'This profile has no state.db yet. Use Hermes for a while, then come back.',
  'error.refused.title': 'Refused',
  'error.invalid.title': 'Hermes rejected the request',
  'error.generic.title': 'Something went wrong',
  'error.generic.body': detail => `${detail || 'Unknown error'}. Retry, or check the gateway logs.`,
  'error.settings.title': 'Could not read the gateway-pricing settings',
  'error.notInstalled.title': 'The gateway-pricing agent plugin is not installed for this profile',
  'error.notInstalled.body':
    'Install the plugin folder under the profile, enable it in Capabilities -> Plugins and reload desktop plugins.',

  'v.baseUrl.empty': 'Enter a base URL like gateway.example.com/openai.',
  'v.baseUrl.chars': 'No spaces, control characters or backslashes.',
  'v.baseUrl.userinfo': "Remove the 'user@' part.",
  'v.baseUrl.query': 'Remove the query string or fragment.',
  'v.baseUrl.port': 'The port must be a number from 0 to 65535.',
  'v.baseUrl.host': 'Enter a host name.',
  'v.baseUrl.star': "'*' is only allowed as a leading '*.' label.",
  'v.baseUrl.starIp': "'*.' cannot be used with an IP address.",
  'v.baseUrl.hostname': host => `"${host}" is not a valid host name.`,
  'v.number': 'Enter a number.',
  'v.nonNegative': 'Must be 0 or more.',
  'v.positive': 'Must be greater than 0.',
  'v.required': 'Required.',
  'v.idDuplicate': 'Another rule already uses this id.',
  'v.vendorModel': "Enter only the model; the vendor comes from the list."
}

function t(key, ...args) {
  const value = STRINGS[key]

  if (typeof value === 'function') {
    return value(...args)
  }

  return value ?? key
}

// ═════════════════════════════════════════════════════════════════════════════════════════════
// Small helpers
// ═════════════════════════════════════════════════════════════════════════════════════════════

const CAPTION = 'text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)'
const ERROR_TEXT = 'text-[length:var(--conversation-caption-font-size)] text-destructive'
const SECTION = 'mt-6 mb-2 text-[length:var(--conversation-text-font-size)] font-medium text-foreground'

const isPlainObject = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const clone = value => (value === undefined ? undefined : JSON.parse(JSON.stringify(value)))
const sameJson = (a, b) => JSON.stringify(a) === JSON.stringify(b)

function formatUsd(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '—'
  }

  const digits = value !== 0 && Math.abs(value) < 0.01 ? 4 : 2

  return `$${value.toFixed(digits)}`
}

function formatTokens(value) {
  const n = Number(value) || 0

  if (n >= 1e6) {
    return `${(n / 1e6).toFixed(1)}M`
  }

  if (n >= 1e3) {
    return `${(n / 1e3).toFixed(1)}k`
  }

  return String(n)
}

function formatRate(value) {
  return typeof value === 'number' ? String(Number(value.toFixed(6))) : String(value)
}

function createStore(initial) {
  let state = initial
  const listeners = new Set()

  return {
    get: () => state,
    set(patch) {
      state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) }
      listeners.forEach(fn => fn())
    },
    subscribe(fn) {
      listeners.add(fn)

      return () => listeners.delete(fn)
    }
  }
}

function useStore(store) {
  return useSyncExternalStore(store.subscribe, store.get, store.get)
}

// "<status>: <body>" at the start, or after the "Error invoking remote method '…': Error: " prefix
// Electron adds when the rejection crosses IPC; "HTTP 404" / "status 404" as a fallback.
const STATUS_PREFIX = /^(?:[\s\S]*?\bError:\s*)?\s*(?:HTTP\s*)?([1-5]\d\d)(?=:|\s|$)\s*:?\s*/i
const STATUS_WORD = /\b(?:HTTP|status)\s*:?\s*([1-5]\d\d)\b/i

/**
 * Turn whatever ctx.rest / host.request rejected with into {kind, status, detail}.
 * Hermes Desktop's REST transport rejects with Error("<status>: <body>") and .statusCode
 * (apps/desktop/electron/api-transport.ts httpStatusError); the body is FastAPI's {"detail": ...}.
 * Across IPC only the message may survive, so both are read.
 */
export function classifyError(error) {
  const message = String(error?.message ?? error ?? '')
  let status = [error?.statusCode, error?.status, error?.response?.status].find(Number.isInteger) ?? null
  let prefix = null

  if (status === null) {
    const found = STATUS_PREFIX.exec(message)

    if (found) {
      status = Number(found[1])
      prefix = found[0]
    } else {
      const word = STATUS_WORD.exec(message)
      status = word ? Number(word[1]) : null
    }
  } else {
    const found = STATUS_PREFIX.exec(message)
    prefix = found && Number(found[1]) === status ? found[0] : null
  }

  let detail = error?.detail ?? error?.body?.detail ?? null

  if (detail === null) {
    const json = message.match(/\{[\s\S]*\}/)

    if (json) {
      try {
        detail = JSON.parse(json[0]).detail ?? null
      } catch {
        detail = null
      }
    }
  }

  if (detail !== null && typeof detail !== 'string') {
    detail = Array.isArray(detail) ? detail.map(d => d?.msg ?? JSON.stringify(d)).join('; ') : JSON.stringify(detail)
  }

  if (detail === null) {
    detail = (prefix ? message.slice(prefix.length) : message).trim() || null
  }

  let kind = 'generic'
  const timedOut = error?.name === 'TimeoutError' || error?.name === 'AbortError' || /timed?[\s-]?out|timeout/i.test(message)

  if (status === null && timedOut) {
    kind = 'timeout'
  } else if (status === 408 || status === 504) {
    kind = 'timeout'
  } else if (status === 404) {
    kind = /state\.db/i.test(detail ?? '') ? 'no-db' : 'not-mounted'
  } else if (status === 503) {
    kind = 'unavailable'
  } else if (status === 409) {
    kind = 'refused'
  } else if (status === 400 || status === 422) {
    kind = 'invalid'
  } else if (status === null && /gateway unavailable|not connected|bridge unavailable/i.test(message)) {
    kind = 'offline'
  }

  return { kind, status, detail }
}

/** Title + next step for a classified error. */
function describeError(info) {
  switch (info.kind) {
    case 'not-mounted':
      return { title: t('error.notMounted.title'), body: t('error.notMounted.body') }
    case 'unavailable':
      return { title: t('error.unavailable.title'), body: t('error.unavailable.body', info.detail) }
    case 'timeout':
      return { title: t('error.timeout.title'), body: t('error.timeout.body') }
    case 'offline':
      return { title: t('error.offline.title'), body: t('error.offline.body') }
    case 'no-db':
      return { title: t('error.noDb.title'), body: t('error.noDb.body') }
    case 'refused':
      return { title: t('error.refused.title'), body: info.detail ?? '' }
    case 'invalid':
      return { title: t('error.invalid.title'), body: info.detail ?? '' }
    case 'not-installed':
      return { title: t('error.notInstalled.title'), body: t('error.notInstalled.body') }
    default:
      return { title: t('error.generic.title'), body: t('error.generic.body', info.detail) }
  }
}

function errorText(info) {
  const { title, body } = describeError(info)

  return body ? `${title}: ${body}` : title
}

// ═════════════════════════════════════════════════════════════════════════════════════════════
// Rule model and validation (mirrors rules.py / match.py; the backend stays the source of truth)
// ═════════════════════════════════════════════════════════════════════════════════════════════

const MODES = ['price_as', 'prices', 'free']
const PRICE_KEYS = ['input', 'output', 'cache_read', 'cache_write']
const RULE_KEYS = new Set(['id', 'enabled', 'match', 'price_as', 'prices', 'free', 'multiplier', 'override'])
const AMBIGUOUS = /[\s\\\x00-\x1f\x7f]/
const LABEL = /^[a-z0-9_]([a-z0-9_-]*[a-z0-9_])?$/
const GLOB_CHARS = /[*?[\]]/

export function ruleMode(raw) {
  return MODES.find(mode => isPlainObject(raw) && mode in raw) ?? 'price_as'
}

export function ruleLabel(raw, index) {
  const id = isPlainObject(raw) && typeof raw.id === 'string' ? raw.id.trim() : ''

  return id || t('rules.label', index + 1)
}

function ruleModels(raw) {
  const models = raw?.match?.models

  if (typeof models === 'string') {
    return models.trim() ? [models] : []
  }

  return Array.isArray(models) ? models.filter(m => typeof m === 'string') : []
}

export function matchSummary(raw) {
  const base = typeof raw?.match?.base_url === 'string' ? raw.match.base_url : '?'
  const models = ruleModels(raw)

  return `${base} · ${models.length ? models.join(', ') : t('rules.allModels')}`
}

export function modeSummary(raw) {
  const mode = ruleMode(raw)
  let text

  if (mode === 'free') {
    text = t('mode.free')
  } else if (mode === 'prices') {
    const p = isPlainObject(raw.prices) ? raw.prices : {}
    text = t('mode.prices', p.input ?? '?', p.output ?? '?')

    if (p.cache_read != null || p.cache_write != null) {
      text += `, ${t('mode.cache', p.cache_read, p.cache_write)}`
    }
  } else {
    const target = typeof raw.price_as === 'string' ? raw.price_as.trim() : ''
    text = !target || target.toLowerCase() === 'auto' ? t('mode.auto') : t('mode.priceAs', target)
  }

  const multiplier = raw?.multiplier

  if (multiplier != null && Number(multiplier) !== 1) {
    text += ` ${t('mode.multiplier', multiplier)}`
  }

  return text
}

/** Raw rule -> editor form (all strings/booleans). Fields the form does not know stay in the raw. */
export function ruleToForm(raw) {
  const rule = isPlainObject(raw) ? raw : {}
  const priceAs = typeof rule.price_as === 'string' ? rule.price_as.trim() : 'auto'
  const slash = priceAs.indexOf('/')
  const prices = isPlainObject(rule.prices) ? rule.prices : {}

  return {
    id: typeof rule.id === 'string' ? rule.id : '',
    enabled: rule.enabled !== false,
    baseUrl: typeof rule.match?.base_url === 'string' ? rule.match.base_url : '',
    models: ruleModels(rule).join(', '),
    mode: ruleMode(rule),
    vendor: !priceAs || priceAs.toLowerCase() === 'auto' ? 'auto' : slash === -1 ? priceAs : priceAs.slice(0, slash),
    vendorModel: slash === -1 ? '' : priceAs.slice(slash + 1),
    prices: Object.fromEntries(PRICE_KEYS.map(key => [key, prices[key] == null ? '' : String(prices[key])])),
    multiplier: rule.multiplier == null ? '' : String(rule.multiplier),
    override: rule.override === true
  }
}

function splitModels(text) {
  return text
    .split(',')
    .map(m => m.trim())
    .filter(Boolean)
}

/**
 * Editor form -> raw rule, merged over the rule being edited: unknown keys at the rule, match and
 * prices level survive, the other modes' keys are dropped, cleared optional fields are removed.
 */
export function formToRule(form, base = {}) {
  const rule = {}
  const id = form.id.trim()

  if (id) {
    rule.id = id
  }

  if (!form.enabled || 'enabled' in base) {
    rule.enabled = form.enabled
  }

  const match = { base_url: form.baseUrl.trim() }
  const models = splitModels(form.models)

  if (models.length) {
    match.models = models
  }

  for (const [key, value] of Object.entries(isPlainObject(base.match) ? base.match : {})) {
    if (key !== 'base_url' && key !== 'models') {
      match[key] = value
    }
  }

  rule.match = match

  if (form.mode === 'free') {
    rule.free = true
  } else if (form.mode === 'prices') {
    const prices = {}

    for (const key of PRICE_KEYS) {
      if (form.prices[key].trim() !== '') {
        prices[key] = Number(form.prices[key])
      }
    }

    for (const [key, value] of Object.entries(isPlainObject(base.prices) ? base.prices : {})) {
      if (!PRICE_KEYS.includes(key)) {
        prices[key] = value
      }
    }

    rule.prices = prices
  } else {
    const model = form.vendorModel.trim()
    rule.price_as = form.vendor === 'auto' ? 'auto' : model ? `${form.vendor}/${model}` : form.vendor
  }

  if (form.multiplier.trim() !== '') {
    rule.multiplier = Number(form.multiplier)
  }

  if (form.override || 'override' in base) {
    rule.override = form.override
  }

  for (const [key, value] of Object.entries(base)) {
    if (!RULE_KEYS.has(key)) {
      rule[key] = value
    }
  }

  return rule
}

function isIpAddress(host) {
  if (host.includes(':')) {
    return true // IPv6 (bracketed in the pattern); the backend does the full check
  }

  const parts = host.split('.')

  return parts.length === 4 && parts.every(p => /^\d{1,3}$/.test(p) && Number(p) <= 255)
}

/** Port of match.parse_pattern: an error message, or null when the backend will accept it. */
export function validateBaseUrl(text) {
  const raw = String(text ?? '').trim()

  if (!raw) {
    return t('v.baseUrl.empty')
  }

  if (AMBIGUOUS.test(raw)) {
    return t('v.baseUrl.chars')
  }

  const rest = raw.includes('://') ? raw.slice(raw.indexOf('://') + 3) : raw
  const cut = rest.search(/[/?#]/)
  const netloc = cut === -1 ? rest : rest.slice(0, cut)
  const tail = cut === -1 ? '' : rest.slice(cut)

  if (netloc.includes('@')) {
    return t('v.baseUrl.userinfo')
  }

  if (/[?#]/.test(tail)) {
    return t('v.baseUrl.query')
  }

  let host = netloc
  let port = null

  if (host.startsWith('[')) {
    const end = host.indexOf(']')

    if (end === -1) {
      return t('v.baseUrl.hostname', host)
    }

    const after = host.slice(end + 1)
    host = host.slice(1, end)

    if (after) {
      if (!after.startsWith(':')) {
        return t('v.baseUrl.hostname', netloc)
      }

      port = after.slice(1)
    }
  } else if (host.includes(':')) {
    port = host.slice(host.lastIndexOf(':') + 1)
    host = host.slice(0, host.lastIndexOf(':'))
  }

  if (port !== null && port !== '' && !(/^\d+$/.test(port) && Number(port) <= 65535)) {
    return t('v.baseUrl.port')
  }

  host = host.toLowerCase().replace(/\.$/, '')

  if (!host) {
    return t('v.baseUrl.host')
  }

  const wildcard = host.startsWith('*.')

  if (wildcard) {
    host = host.slice(2)
  }

  if (host.includes('*')) {
    return t('v.baseUrl.star')
  }

  if (isIpAddress(host)) {
    return wildcard ? t('v.baseUrl.starIp') : null
  }

  if (host.length > 253 || !host.split('.').every(label => LABEL.test(label))) {
    return t('v.baseUrl.hostname', host)
  }

  return null
}

function numberError(text, { required = false, positive = false } = {}) {
  const raw = text.trim()

  if (!raw) {
    return required ? t('v.required') : null
  }

  const value = Number(raw)

  if (!Number.isFinite(value)) {
    return t('v.number')
  }

  if (positive && value <= 0) {
    return t('v.positive')
  }

  return value < 0 ? t('v.nonNegative') : null
}

/** Field -> message for everything rules.py would reject. `otherIds`: ids of the other rules. */
export function validateForm(form, otherIds = []) {
  const errors = {}
  const put = (key, message) => message && (errors[key] = message)

  put('baseUrl', validateBaseUrl(form.baseUrl))

  if (form.id.trim() && otherIds.includes(form.id.trim())) {
    put('id', t('v.idDuplicate'))
  }

  if (form.mode === 'prices') {
    for (const key of PRICE_KEYS) {
      put(`prices.${key}`, numberError(form.prices[key], { required: key === 'input' || key === 'output' }))
    }
  }

  if (form.mode === 'price_as' && form.vendor !== 'auto' && form.vendorModel.includes('/')) {
    put('vendorModel', t('v.vendorModel'))
  }

  put('multiplier', numberError(form.multiplier, { positive: true }))

  return errors
}

/** Preview defaults: the rule's own host and its first model glob without wildcards. */
export function previewDefaults(form) {
  const model = splitModels(form.models).find(m => !GLOB_CHARS.test(m)) ?? ''
  const base = form.baseUrl.trim().replace(/^(\w+:\/\/)?\*\./, (_, scheme = '') => `${scheme}api.`)

  return { model, baseUrl: base }
}

/** The rule a detected endpoint would need, pre-filled from the backend's suggestion. */
export function ruleFromDetected(row) {
  const url = String(row.base_url ?? '')
  const rest = url.includes('://') ? url.slice(url.indexOf('://') + 3) : url
  const base = rest.replace(/[?#].*$/, '').replace(/\/+$/, '') || row.host || ''
  const rule = { match: { base_url: base, models: [row.model] } }
  const suggestion = row.suggestion

  if (suggestion?.mode === 'free') {
    rule.free = true
  } else if (suggestion?.mode === 'price_as' && suggestion.price_as) {
    rule.price_as = suggestion.price_as
  } else {
    rule.prices = {}
  }

  return rule
}

/** Backend warnings ("rules[3] (id): ...; rule skipped") keyed by rule index, plus the rest. */
export function splitWarnings(warnings) {
  const byRule = {}
  const general = []

  for (const warning of Array.isArray(warnings) ? warnings : []) {
    const found = /^rules\[(\d+)\]/.exec(warning)

    if (found) {
      ;(byRule[Number(found[1])] ??= []).push(warning)
    } else {
      general.push(warning)
    }
  }

  return { byRule, general }
}

function aliasRowsFrom(aliases, nextId) {
  return Object.entries(isPlainObject(aliases) ? aliases : {}).map(([from, to]) => ({
    key: nextId(),
    from,
    to: typeof to === 'string' ? to : String(to)
  }))
}

/** Problems that keep the alias rows from becoming a valid mapping. */
export function aliasProblems(rows) {
  const problems = {}
  const seen = new Map()

  rows.forEach((row, index) => {
    const from = row.from.trim()
    const to = row.to.trim()

    if (!from && !to) {
      return
    }

    if (!from || !to) {
      problems[index] = t('aliases.incomplete')

      return
    }

    if (seen.has(from)) {
      problems[index] = t('aliases.duplicate', from)
      problems[seen.get(from)] = t('aliases.duplicate', from)
    } else {
      seen.set(from, index)
    }
  })

  return problems
}

function aliasesFromRows(rows) {
  const out = {}

  for (const row of rows) {
    if (row.from.trim() && row.to.trim()) {
      out[row.from.trim()] = row.to.trim()
    }
  }

  return out
}

function normalizeValues(values) {
  return {
    rules: Array.isArray(values?.rules) ? clone(values.rules) : [],
    aliases: isPlainObject(values?.aliases) ? clone(values.aliases) : {},
    enabled: values?.enabled !== false
  }
}

// ═════════════════════════════════════════════════════════════════════════════════════════════
// State and backend: one controller per register(); every page reads it with useStore().
// ═════════════════════════════════════════════════════════════════════════════════════════════

function createController(ctx) {
  let seq = 0
  let disposed = false
  let saving = null // the values being written, while a save is in flight
  let recheckAfterSave = false
  const nextKey = () => `a${++seq}`
  const idle = { state: 'idle', data: null, error: null }

  const store = createStore({
    profile: null,
    settings: idle, // plugins.manage list
    saved: null, // {rules, aliases, enabled} as last read or written
    rules: [],
    aliasRows: [],
    enabled: true,
    status: idle,
    detected: idle,
    lint: null, // backend warnings for the draft rules ({rules, warnings})
    saveState: { state: 'idle', error: null },
    remote: null, // values changed elsewhere while the draft is dirty
    profileNotice: null,
    editor: null, // {index: number | null, base: rule}
    backfill: { days: '30', preview: idle, result: null }
  })

  const currentProfile = () => {
    const value = host?.state?.profile?.get?.()

    return typeof value === 'string' && value.trim() ? value.trim() : null
  }

  const withProfile = (params, profile) => (profile ? { ...params, profile } : params)

  const rest = (path, opts = {}) => ctx.rest(path, { timeoutMs: REQUEST_TIMEOUT_MS, ...opts })

  const request = (params, timeoutMs = REQUEST_TIMEOUT_MS) => {
    if (typeof host?.request !== 'function') {
      return Promise.reject(new Error('Hermes gateway unavailable'))
    }

    return host.request('plugins.manage', params, timeoutMs)
  }

  const set = patch => !disposed && store.set(patch)

  function values(state = store.get()) {
    return { rules: state.rules, aliases: aliasesFromRows(state.aliasRows), enabled: state.enabled }
  }

  function isDirty(state = store.get()) {
    return (
      state.saved !== null &&
      (!sameJson(values(state), state.saved) || Object.keys(aliasProblems(state.aliasRows)).length > 0)
    )
  }

  function adopt(saved) {
    set({
      saved,
      rules: clone(saved.rules),
      aliasRows: aliasRowsFrom(saved.aliases, nextKey),
      enabled: saved.enabled,
      remote: null,
      profileNotice: null,
      lint: null,
      saveState: { state: 'idle', error: null }
    })
  }

  async function readSettings(profile) {
    const result = await request(withProfile({ action: 'list' }, profile))
    const row = (result?.plugins ?? []).find(r => r?.key === PLUGIN_ID || r?.name === PLUGIN_ID)

    if (!row) {
      throw Object.assign(new Error('gateway-pricing is not installed'), { notInstalled: true })
    }

    const fields = Array.isArray(row.settings_schema) ? row.settings_schema : []
    const pick = (key, fallback) => {
      const field = fields.find(f => f?.key === key)

      return field ? (field.value ?? field.default ?? fallback) : fallback
    }

    return normalizeValues({ rules: pick('rules', []), aliases: pick('aliases', {}), enabled: pick('enabled', true) })
  }

  const classify = error => (error?.notInstalled ? { kind: 'not-installed', status: null, detail: null } : classifyError(error))

  async function loadSettings() {
    const profile = currentProfile()
    set({ profile, settings: { ...store.get().settings, state: 'loading' } })

    try {
      const saved = await readSettings(profile)

      if (profile !== currentProfile()) {
        return
      }

      set({ settings: { state: 'ready', data: true, error: null } })
      adopt(saved)
    } catch (error) {
      set({ settings: { state: 'error', data: null, error: classify(error) } })
    }
  }

  async function loadStatus() {
    set({ status: { ...store.get().status, state: 'loading' } })

    try {
      const data = await rest('/status')
      set({ status: { state: 'ready', data, error: null } })
    } catch (error) {
      set({ status: { state: 'error', data: null, error: classifyError(error) } })
    }
  }

  async function loadDetected() {
    set({ detected: { ...store.get().detected, state: 'loading' } })

    try {
      const data = await rest(`/detected?days=${DETECT_DAYS}`)
      set({ detected: { state: 'ready', data: Array.isArray(data) ? data : [], error: null } })
    } catch (error) {
      set({ detected: { state: 'error', data: null, error: classifyError(error) } })
    }
  }

  function loadAll() {
    return Promise.all([loadSettings(), loadStatus(), loadDetected()])
  }

  // Backend warnings for the draft: /preview parses the draft with the real rules.py.
  let lintTimer = null

  function scheduleLint() {
    lintTimer?.()
    lintTimer = null
    const state = store.get()

    if (!isDirty(state) || sameJson(state.rules, state.saved?.rules)) {
      set({ lint: null })

      return
    }

    const rules = state.rules
    lintTimer = ctx.setTimeout(() => {
      lintTimer = null
      rest('/preview', {
        method: 'POST',
        body: { model: 'lint', base_url: 'https://lint.invalid', rules, aliases: aliasesFromRows(store.get().aliasRows) }
      }).then(
        data => store.get().rules === rules && set({ lint: { rules, warnings: data?.warnings ?? [] } }),
        () => store.get().rules === rules && set({ lint: null })
      )
    }, PREVIEW_DEBOUNCE_MS)
  }

  function editDraft(patch) {
    set(patch)
    scheduleLint()
  }

  async function save() {
    const state = store.get()

    if (!isDirty(state) || saving || Object.keys(aliasProblems(state.aliasRows)).length) {
      return
    }

    const next = values(state)
    saving = next
    set({ saveState: { state: 'saving', error: null } })

    try {
      const result = await request(
        withProfile({ action: 'settings', key: PLUGIN_ID, values: { rules: next.rules, aliases: next.aliases, enabled: next.enabled } }, state.profile)
      )

      if (result && result.ok === false) {
        throw new Error(result.error || 'refused')
      }

      const savedValues = clone(next)
      set({ saved: savedValues, saveState: { state: 'idle', error: null }, remote: null, profileNotice: null, lint: null })
      invalidateBackfill()
      loadStatus()
      loadDetected()

      if (state.profile !== currentProfile()) {
        loadAll()
      }
    } catch (error) {
      const info = classifyError(error)
      set({ saveState: { state: 'error', error: info.kind === 'timeout' ? errorText(info) : info.detail || errorText(info) } })
    } finally {
      saving = null

      if (recheckAfterSave) {
        recheckAfterSave = false
        onSettingsChanged()
      }
    }
  }

  function discard() {
    const state = store.get()

    if (state.saved) {
      adopt(state.remote ?? state.saved)
    }

    if (state.profile !== currentProfile()) {
      loadAll()
    }
  }

  // Hermes broadcasts the event for every save, ours included, and for any profile: re-read and
  // compare instead of trusting it.
  async function onSettingsChanged() {
    if (saving) {
      recheckAfterSave = true

      return
    }

    const state = store.get()

    if (state.settings.state !== 'ready' || state.profile !== currentProfile()) {
      return
    }

    let fresh

    try {
      fresh = await readSettings(state.profile)
    } catch {
      return
    }

    const now = store.get()

    if (disposed || saving || now.profile !== state.profile || sameJson(fresh, now.saved)) {
      return
    }

    if (isDirty(now)) {
      set({ remote: fresh })
    } else {
      adopt(fresh)
      invalidateBackfill()
      loadStatus()
      loadDetected()
    }
  }

  function keepMine() {
    const state = store.get()

    if (state.remote) {
      set({ saved: state.remote, remote: null })
    }
  }

  function onProfileChanged() {
    const state = store.get()
    const next = currentProfile()

    if (state.settings.state === 'idle' || next === state.profile) {
      return
    }

    if (isDirty(state)) {
      set({ profileNotice: { from: state.profile, to: next } })
    } else {
      invalidateBackfill()
      loadAll()
    }
  }

  // ── rules ──
  function setRules(rules) {
    editDraft({ rules })
  }

  function moveRule(index, delta) {
    const rules = store.get().rules.slice()
    const target = index + delta

    if (target < 0 || target >= rules.length) {
      return
    }

    ;[rules[index], rules[target]] = [rules[target], rules[index]]
    setRules(rules)
  }

  function duplicateRule(index) {
    const rules = store.get().rules.slice()
    const copy = clone(rules[index])

    if (isPlainObject(copy) && typeof copy.id === 'string' && copy.id.trim()) {
      const ids = new Set(rules.map(r => r?.id))
      let n = 2

      while (ids.has(`${copy.id}-${n}`)) {
        n += 1
      }

      copy.id = `${copy.id}-${n}`
    }

    rules.splice(index + 1, 0, copy)
    setRules(rules)
  }

  function deleteRule(index) {
    setRules(store.get().rules.filter((_, i) => i !== index))
  }

  function toggleRule(index, on) {
    const rules = store.get().rules.slice()
    const rule = { ...rules[index] }

    if (on && !('enabled' in (store.get().saved?.rules?.[index] ?? {}))) {
      delete rule.enabled
    } else {
      rule.enabled = on
    }

    rules[index] = rule
    setRules(rules)
  }

  function openEditor(index, base) {
    set({ editor: { index, base: clone(base ?? store.get().rules[index] ?? {}), token: ++seq } })
  }

  function closeEditor() {
    set({ editor: null })
  }

  function commitEditor(rule) {
    const { editor, rules } = store.get()

    if (!editor) {
      return
    }

    const next = rules.slice()

    if (editor.index === null) {
      next.push(rule)
    } else {
      next[editor.index] = rule
    }

    set({ editor: null })
    setRules(next)
  }

  // ── aliases ──
  function setAliasRows(aliasRows) {
    editDraft({ aliasRows })
  }

  function addAlias() {
    setAliasRows([...store.get().aliasRows, { key: nextKey(), from: '', to: '' }])
  }

  // ── backfill (saved rules only: never sends draft rules) ──
  let backfillSeq = 0

  function invalidateBackfill() {
    backfillSeq += 1
    set(state => ({ backfill: { ...state.backfill, preview: idle } }))
  }

  function setBackfillDays(days) {
    backfillSeq += 1
    set({ backfill: { days, preview: idle, result: null } })
  }

  const backfillBody = extra => {
    const days = store.get().backfill.days

    return days === 'all' ? extra : { ...extra, days: Number(days) }
  }

  async function backfillPreview() {
    const mine = ++backfillSeq
    set(state => ({ backfill: { ...state.backfill, preview: { ...idle, state: 'loading' }, result: null } }))

    try {
      const data = await rest('/backfill', { method: 'POST', body: backfillBody({ dry_run: true }) })

      if (mine === backfillSeq) {
        set(state => ({ backfill: { ...state.backfill, preview: { state: 'ready', data, error: null } } }))
      }
    } catch (error) {
      if (mine === backfillSeq) {
        set(state => ({ backfill: { ...state.backfill, preview: { state: 'error', data: null, error: classifyError(error) } } }))
      }
    }
  }

  /** Throws a readable Error on refusal so ConfirmDialog shows it inline and stays open. */
  async function backfillApply() {
    let data

    try {
      data = await rest('/backfill', {
        method: 'POST',
        body: backfillBody({ dry_run: false, confirm: true }),
        timeoutMs: APPLY_TIMEOUT_MS
      })
    } catch (error) {
      throw new Error(errorText(classifyError(error)))
    }

    backfillSeq += 1
    set(state => ({ backfill: { ...state.backfill, preview: idle, result: data } }))
    loadDetected()
  }

  function ensureLoaded() {
    if (store.get().settings.state === 'idle') {
      loadAll()
    }
  }

  function dispose() {
    disposed = true
    lintTimer?.()
  }

  return {
    store,
    rest,
    schedule: (fn, ms) => ctx.setTimeout(fn, ms),
    isDirty,
    values,
    ensureLoaded,
    loadAll,
    loadStatus,
    loadDetected,
    loadSettings,
    save,
    discard,
    keepMine,
    onSettingsChanged,
    onProfileChanged,
    setEnabled: enabled => editDraft({ enabled }),
    moveRule,
    duplicateRule,
    deleteRule,
    toggleRule,
    openEditor,
    closeEditor,
    commitEditor,
    setAliasRows,
    addAlias,
    setBackfillDays,
    backfillPreview,
    backfillApply,
    dispose
  }
}

// ═════════════════════════════════════════════════════════════════════════════════════════════
// Shared UI
// ═════════════════════════════════════════════════════════════════════════════════════════════

function Field({ id, label, hint, error, children }) {
  return jsxs('div', {
    className: 'grid gap-1',
    children: [
      jsx('label', { className: 'text-[length:var(--conversation-caption-font-size)] font-medium text-foreground', htmlFor: id, children: label }),
      children,
      error
        ? jsx('div', { className: ERROR_TEXT, id: `${id}-error`, children: error })
        : hint
          ? jsx('div', { className: CAPTION, id: `${id}-hint`, children: hint })
          : null
    ]
  })
}

function TextField({ id, label, hint, error, value, onChange, placeholder, inputMode }) {
  return jsx(Field, {
    error,
    hint,
    id,
    label,
    children: jsx(Input, {
      'aria-describedby': error ? `${id}-error` : hint ? `${id}-hint` : undefined,
      'aria-invalid': error ? true : undefined,
      id,
      inputMode,
      onChange: event => onChange(event.target.value),
      placeholder,
      value
    })
  })
}

function ErrorPanel({ info, onRetry }) {
  const { title, body } = describeError(info)

  return jsx('div', {
    className: 'py-6',
    role: 'alert',
    children: jsx(ErrorState, {
      description: body,
      title,
      children: onRetry ? jsx(Button, { onClick: onRetry, type: 'button', variant: 'secondary', children: t('common.retry') }) : undefined
    })
  })
}

function Notice({ children, actions, tone = 'muted' }) {
  return jsxs('div', {
    className:
      'my-3 flex flex-wrap items-center gap-3 rounded-md border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) px-3 py-2 text-[length:var(--conversation-caption-font-size)]',
    role: tone === 'alert' ? 'alert' : 'status',
    children: [jsx('div', { className: 'min-w-0 flex-1 text-foreground', children }), actions ? jsx('div', { className: 'flex gap-2', children: actions }) : null]
  })
}

function DirtyBar({ app }) {
  const state = useStore(app.store)

  if (!app.isDirty(state)) {
    return null
  }

  const problems = Object.keys(aliasProblems(state.aliasRows)).length > 0
  const busy = state.saveState.state === 'saving'

  return jsxs('div', {
    'aria-label': t('dirty.label'),
    className:
      'sticky bottom-0 z-10 mt-6 grid gap-2 border-t border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) px-3 py-2 text-[length:var(--conversation-caption-font-size)]',
    role: 'region',
    children: [
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-3',
        children: [
          jsx('span', { className: 'min-w-0 flex-1 text-foreground', children: problems ? t('dirty.aliasErrors') : t('dirty.message') }),
          jsx(Button, { disabled: busy, onClick: app.discard, type: 'button', variant: 'ghost', children: t('common.discard') }),
          jsx(Button, { disabled: busy || problems, onClick: app.save, type: 'button', children: busy ? t('common.saving') : t('common.save') })
        ]
      }),
      state.saveState.state === 'error'
        ? jsxs('div', {
            className: ERROR_TEXT,
            role: 'alert',
            children: [t('dirty.saveFailed', state.saveState.error), ' ', t('dirty.saveHint')]
          })
        : null
    ]
  })
}

/** Loads on first mount, shows notices and the dirty bar, and never renders a blank page. */
function PageFrame({ app, children }) {
  const state = useStore(app.store)

  useEffect(() => {
    app.ensureLoaded()
  }, [app])

  let body

  if (state.settings.state === 'error') {
    body = jsx(ErrorPanel, { info: state.settings.error, onRetry: app.loadAll })
  } else if (state.saved === null) {
    body = jsx('div', { 'aria-busy': true, className: `py-6 ${CAPTION}`, role: 'status', children: t('common.loading') })
  } else {
    body = children
  }

  return jsxs('div', {
    className: 'grid gap-1',
    children: [
      state.remote
        ? jsx(Notice, {
            tone: 'alert',
            actions: [
              jsx(Button, { key: 'reload', onClick: app.discard, size: 'sm', type: 'button', variant: 'secondary', children: t('remote.reload') }),
              jsx(Button, { key: 'keep', onClick: app.keepMine, size: 'sm', type: 'button', variant: 'ghost', children: t('remote.keep') })
            ],
            children: t('remote.message')
          })
        : null,
      state.profileNotice ? jsx(Notice, { tone: 'alert', children: t('profile.changed', state.profileNotice.from, state.profileNotice.to) }) : null,
      body,
      jsx(EditorHost, { app }),
      jsx(DirtyBar, { app })
    ]
  })
}

// ═════════════════════════════════════════════════════════════════════════════════════════════
// Rule editor (dialog) with live preview
// ═════════════════════════════════════════════════════════════════════════════════════════════

function EditorHost({ app }) {
  const { editor } = useStore(app.store)

  if (!editor) {
    return null
  }

  return jsx(RuleEditor, { app, editor }, editor.token)
}

function RuleEditor({ app, editor }) {
  const state = useStore(app.store)
  const initial = useRef(ruleToForm(editor.base))
  const [form, setForm] = useState(initial.current)
  const uid = useId()
  const id = name => `${uid}-${name}`
  const update = patch => setForm(prev => ({ ...prev, ...patch }))
  const otherIds = state.rules.filter((_, i) => i !== editor.index).map(r => (typeof r?.id === 'string' ? r.id.trim() : '')).filter(Boolean)
  const errors = validateForm(form, otherIds)
  const invalid = Object.keys(errors).length > 0
  const vendors = ['auto', ...(state.status.data?.vendors ?? [])]

  if (!vendors.includes(form.vendor)) {
    vendors.push(form.vendor)
  }

  // An untouched form keeps the raw rule byte for byte (string prices, alias spellings, key order).
  const candidate = sameJson(form, initial.current) && editor.index !== null ? editor.base : formToRule(form, editor.base)
  const commit = () => !invalid && app.commitEditor(candidate)

  return jsx(Dialog, {
    onOpenChange: open => !open && app.closeEditor(),
    open: true,
    children: jsxs(DialogContent, {
      className: 'max-h-[85vh] max-w-xl overflow-y-auto',
      children: [
        jsxs(DialogHeader, {
          children: [
            jsx(DialogTitle, { children: editor.index === null ? t('editor.add') : t('editor.edit') }),
            jsx(DialogDescription, { children: t('editor.description') })
          ]
        }),
        jsxs('div', {
          className: 'grid gap-3',
          children: [
            jsx(TextField, {
              error: errors.baseUrl,
              hint: t('editor.baseUrlHint'),
              id: id('base'),
              label: t('editor.baseUrl'),
              onChange: baseUrl => update({ baseUrl }),
              placeholder: 'gateway.example.com/openai',
              value: form.baseUrl
            }),
            jsx(TextField, {
              hint: t('editor.modelsHint'),
              id: id('models'),
              label: t('editor.models'),
              onChange: models => update({ models }),
              placeholder: 'claude-*, gpt-5*',
              value: form.models
            }),
            jsxs('div', {
              'aria-labelledby': id('mode'),
              className: 'grid gap-1',
              role: 'group',
              children: [
                jsx('span', { className: 'text-[length:var(--conversation-caption-font-size)] font-medium text-foreground', id: id('mode'), children: t('editor.mode') }),
                jsx(SegmentedControl, {
                  onChange: mode => update({ mode }),
                  options: [
                    { id: 'price_as', label: t('editor.modePriceAs') },
                    { id: 'prices', label: t('editor.modePrices') },
                    { id: 'free', label: t('editor.modeFree') }
                  ],
                  sizing: 'content',
                  value: form.mode
                })
              ]
            }),
            form.mode === 'price_as' ? jsx(VendorFields, { errors, form, id, update, vendors }) : null,
            form.mode === 'prices'
              ? jsxs('div', {
                  className: 'grid gap-2',
                  children: [
                    jsx('div', { className: 'grid grid-cols-2 gap-2', children: PRICE_KEYS.map(key =>
                      jsx(TextField, {
                        error: errors[`prices.${key}`],
                        id: id(`price-${key}`),
                        inputMode: 'decimal',
                        label: t(`editor.price.${key}`),
                        onChange: value => update({ prices: { ...form.prices, [key]: value } }),
                        placeholder: '0.00',
                        value: form.prices[key]
                      }, key)
                    ) }),
                    jsx('div', { className: CAPTION, children: t('editor.pricesHint') })
                  ]
                })
              : null,
            form.mode === 'free' ? jsx('div', { className: CAPTION, children: t('editor.freeHint') }) : null,
            jsx(TextField, {
              error: errors.multiplier,
              hint: t('editor.multiplierHint'),
              id: id('multiplier'),
              inputMode: 'decimal',
              label: t('editor.multiplier'),
              onChange: multiplier => update({ multiplier }),
              placeholder: '1',
              value: form.multiplier
            }),
            jsx(ToggleRow, { checked: form.override, description: t('editor.overrideHint'), label: t('editor.override'), onChange: override => update({ override }) }),
            jsx(ToggleRow, { checked: form.enabled, label: t('editor.enabled'), onChange: enabled => update({ enabled }) }),
            jsx(TextField, {
              error: errors.id,
              hint: t('editor.idHint'),
              id: id('rule-id'),
              label: t('editor.id'),
              onChange: value => update({ id: value }),
              placeholder: 'my-gateway',
              value: form.id
            }),
            jsx(RulePreview, { app, candidate, form, index: editor.index })
          ]
        }),
        jsxs(DialogFooter, {
          children: [
            invalid ? jsx('span', { className: `mr-auto ${ERROR_TEXT}`, children: t('editor.fixErrors') }) : null,
            jsx(Button, { onClick: app.closeEditor, type: 'button', variant: 'ghost', children: t('common.cancel') }),
            jsx(Button, { disabled: invalid, onClick: commit, type: 'button', children: t('common.done') })
          ]
        })
      ]
    })
  })
}

function VendorFields({ errors, form, id, update, vendors }) {
  return jsxs('div', {
    className: 'grid grid-cols-2 gap-2',
    children: [
      jsx(Field, {
        id: id('vendor'),
        label: t('editor.vendor'),
        children: jsxs(Select, {
          onValueChange: vendor => update({ vendor }),
          value: form.vendor,
          children: [
            jsx(SelectTrigger, { 'aria-label': t('editor.vendor'), id: id('vendor'), children: jsx(SelectValue, {}) }),
            jsx(SelectContent, {
              children: vendors.map(v => jsx(SelectItem, { value: v, children: v === 'auto' ? t('editor.vendorAuto') : v }, v))
            })
          ]
        })
      }),
      form.vendor === 'auto'
        ? null
        : jsx(TextField, {
            error: errors.vendorModel,
            hint: t('editor.vendorModelHint'),
            id: id('vendor-model'),
            label: t('editor.vendorModel'),
            onChange: vendorModel => update({ vendorModel }),
            value: form.vendorModel
          })
    ]
  })
}

function RulePreview({ app, candidate, form, index }) {
  const state = useStore(app.store)
  const uid = useId()
  const defaults = previewDefaults(form)
  const [model, setModel] = useState(null) // null: follow the rule's defaults
  const [baseUrl, setBaseUrl] = useState(null)
  const [result, setResult] = useState({ state: 'idle' })
  const latest = useRef(0)
  const mounted = useRef(true)
  const effectiveModel = (model ?? defaults.model).trim()
  const effectiveUrl = (baseUrl ?? defaults.baseUrl).trim()
  const rules = state.rules.slice()
  const position = index === null ? rules.length : index

  rules[position] = candidate

  const aliases = aliasesFromRows(state.aliasRows)
  const payloadKey = JSON.stringify([effectiveModel, effectiveUrl, rules, aliases])

  useEffect(() => () => {
    mounted.current = false
  }, [])

  useEffect(() => {
    const mine = ++latest.current

    if (!effectiveModel || !effectiveUrl) {
      setResult({ state: 'idle' })

      return undefined
    }

    setResult(prev => ({ ...prev, state: 'loading' }))

    return app.schedule(() => {
      app
        .rest('/preview', { method: 'POST', body: { model: effectiveModel, base_url: effectiveUrl, rules, aliases } })
        .then(
          data => mounted.current && mine === latest.current && setResult({ state: 'ready', data }),
          error => mounted.current && mine === latest.current && setResult({ state: 'error', error: classifyError(error) })
        )
    }, PREVIEW_DEBOUNCE_MS)
    // payloadKey captures every input of the request
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payloadKey])

  const data = result.data
  const matched = data?.matched_rule
  const matchedIndex = typeof matched === 'number' ? matched : rules.findIndex(r => r?.id === matched)
  const mine = (data?.warnings ?? []).filter(w => w.startsWith(`rules[${position}]`))
  const rows = []
  const row = (label, value) => rows.push(jsxs('div', { className: 'contents', children: [jsx('dt', { className: 'text-(--ui-text-tertiary)', children: label }), jsx('dd', { className: 'text-foreground', children: value })] }, label))

  if (result.state === 'ready' && data) {
    row(
      t('preview.matched'),
      matched === null || matched === undefined
        ? t('preview.noMatch')
        : `${matchedIndex >= 0 ? ruleLabel(rules[matchedIndex], matchedIndex) : String(matched)} ${matchedIndex === position ? t('preview.thisRule') : t('preview.otherRule')}`
    )
    row(t('preview.status'), data.status)
    row(t('preview.source'), data.source)
    row(t('preview.amount'), formatUsd(data.amount_usd))

    if (data.route?.provider) {
      row(t('preview.route'), `${data.route.provider} / ${data.route.model ?? ''}`)
    }

    const rates = PRICE_KEYS.filter(k => typeof data.per_million?.[k] === 'number').map(k => `${k.replace('_', ' ')} ${formatUsd(data.per_million[k])}`)

    if (rates.length) {
      row(t('preview.rates'), rates.join(' · '))
    }

    if (data.reason) {
      row(t('preview.reason'), data.reason)
    }
  }

  return jsxs('section', {
    'aria-labelledby': `${uid}-title`,
    className: 'mt-2 grid gap-2 rounded-md border border-(--ui-stroke-secondary) p-3',
    children: [
      jsx('h3', { className: 'text-[length:var(--conversation-text-font-size)] font-medium text-foreground', id: `${uid}-title`, children: t('preview.title') }),
      jsxs('div', {
        className: 'grid grid-cols-2 gap-2',
        children: [
          jsx(TextField, { id: `${uid}-model`, label: t('preview.model'), onChange: setModel, value: model ?? defaults.model }),
          jsx(TextField, { id: `${uid}-url`, label: t('preview.baseUrl'), onChange: setBaseUrl, value: baseUrl ?? defaults.baseUrl })
        ]
      }),
      jsxs('div', {
        'aria-live': 'polite',
        className: `grid gap-1 ${CAPTION}`,
        children: [
          result.state === 'idle' ? jsx('div', { children: t('preview.needInput') }) : null,
          result.state === 'loading' ? jsx('div', { children: t('preview.loading') }) : null,
          result.state === 'error'
            ? jsx('div', { className: ERROR_TEXT, role: 'alert', children: `${t('preview.failed')}: ${result.error.detail || errorText(result.error)}` })
            : null,
          rows.length ? jsx('dl', { className: 'grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5', children: rows }) : null,
          ...mine.map(w => jsx('div', { className: ERROR_TEXT, children: w }, w))
        ]
      })
    ]
  })
}

// ═════════════════════════════════════════════════════════════════════════════════════════════
// Pages
// ═════════════════════════════════════════════════════════════════════════════════════════════

function StatusBanner({ app }) {
  const state = useStore(app.store)
  const { status } = state

  if (status.state === 'error') {
    return jsx(ErrorPanel, { info: status.error, onRetry: app.loadStatus })
  }

  if (!status.data) {
    return jsx('div', { 'aria-busy': true, className: `py-3 ${CAPTION}`, role: 'status', children: t('common.loading') })
  }

  const data = status.data
  const warnings = Array.isArray(data.warnings) ? data.warnings : []

  return jsxs('section', {
    'aria-label': t('status.title'),
    children: [
      jsx(ListRow, {
        description: jsxs('span', {
          children: [
            t('status.meta', state.profile, data.plugin_version, data.hermes_version),
            ' · ',
            t('status.rules', data.rules_valid ?? 0, data.rules_total ?? 0)
          ]
        }),
        hint: data.active ? undefined : t('status.inactiveNext'),
        title: jsxs('span', {
          className: 'inline-flex items-center gap-2',
          children: [
            jsx(StatusDot, { tone: data.active ? 'good' : 'bad' }),
            data.active ? t('status.active') : `${t('status.inactive')}: ${data.reason || ''}`
          ]
        })
      }),
      warnings.length
        ? jsxs('div', {
            className: 'mb-2',
            children: [
              jsx('div', { className: CAPTION, children: t('status.warnings') }),
              jsx('ul', { className: `list-disc pl-5 ${ERROR_TEXT}`, children: warnings.map((w, i) => jsx('li', { children: w }, i)) })
            ]
          })
        : null
    ]
  })
}

function suggestionText(suggestion) {
  if (suggestion.mode === 'free') {
    return t('mode.free')
  }

  return t('mode.priceAs', suggestion.price_as)
}

function DetectedList({ app }) {
  const { detected } = useStore(app.store)

  if (detected.state === 'error') {
    if (detected.error.kind === 'no-db') {
      return jsx(EmptyState, { description: t('error.noDb.body'), title: t('error.noDb.title') })
    }

    return jsx(ErrorPanel, { info: detected.error, onRetry: app.loadDetected })
  }

  if (!detected.data) {
    return jsx('div', { 'aria-busy': true, className: `py-3 ${CAPTION}`, role: 'status', children: t('common.loading') })
  }

  const unpriced = detected.data.filter(row => row.matched_rule === null || row.matched_rule === undefined)
  const matched = detected.data.length - unpriced.length

  return jsxs('div', {
    children: [
      unpriced.length === 0
        ? jsx(EmptyState, { description: t('overview.emptyBody', DETECT_DAYS), title: t('overview.emptyTitle') })
        : jsx('div', {
            role: 'list',
            children: unpriced.map(row =>
              jsx('div', {
                role: 'listitem',
                children: jsx(ListRow, {
                  action: jsx(Button, {
                    'aria-label': t('overview.addRuleFor', row.model),
                    onClick: () => app.openEditor(null, ruleFromDetected(row)),
                    size: 'sm',
                    type: 'button',
                    variant: 'secondary',
                    children: t('overview.addRule')
                  }),
                  description: t(
                    'overview.usage',
                    row.host,
                    row.sessions ?? 0,
                    formatTokens((row.input_tokens ?? 0) + (row.output_tokens ?? 0) + (row.cache_read_tokens ?? 0))
                  ),
                  hint: row.base_url,
                  title: row.model,
                  below: jsx('div', {
                    className: `mt-1 ${CAPTION}`,
                    children: row.suggestion
                      ? `${t('overview.suggestion', suggestionText(row.suggestion), row.suggestion.confidence)}. ${row.suggestion.reason ?? ''}`
                      : t('overview.noSuggestion')
                  })
                })
              }, `${row.host}|${row.model}`)
            )
          }),
      matched > 0 ? jsx('div', { className: `mt-2 ${CAPTION}`, children: t('overview.matchedHint', matched) }) : null
    ]
  })
}

function OverviewPage({ app }) {
  const state = useStore(app.store)

  return jsx(PageFrame, {
    app,
    children: jsxs('div', {
      children: [
        jsx(StatusBanner, { app }),
        jsx(ToggleRow, { checked: state.enabled, description: t('overview.enabledHint'), label: t('overview.enabled'), onChange: app.setEnabled }),
        jsx('h3', { className: SECTION, children: t('overview.unpriced') }),
        jsx('div', { className: CAPTION, children: t('overview.unpricedHint', DETECT_DAYS) }),
        jsx(DetectedList, { app })
      ]
    })
  })
}

function IconButton({ label, onClick, disabled, children }) {
  return jsx(Button, { 'aria-label': label, disabled, onClick, size: 'xs', title: label, type: 'button', variant: 'ghost', children })
}

function RuleRow({ app, rule, index, count, warnings }) {
  const [confirming, setConfirming] = useState(false)
  const label = ruleLabel(rule, index)

  return jsxs('div', {
    role: 'listitem',
    children: [
      jsx(ListRow, {
        action: jsxs('div', {
          className: 'flex items-center gap-1',
          children: [
            jsx(Switch, { 'aria-label': t('rules.enable', label), checked: rule?.enabled !== false, onCheckedChange: on => app.toggleRule(index, on) }),
            jsx(IconButton, { disabled: index === 0, label: t('rules.up', label), onClick: () => app.moveRule(index, -1), children: '↑' }),
            jsx(IconButton, { disabled: index === count - 1, label: t('rules.down', label), onClick: () => app.moveRule(index, 1), children: '↓' }),
            jsx(IconButton, { label: t('rules.edit', label), onClick: () => app.openEditor(index), children: t('rules.editShort') }),
            jsx(IconButton, { label: t('rules.duplicate', label), onClick: () => app.duplicateRule(index), children: t('rules.duplicateShort') }),
            jsx(IconButton, { label: t('rules.delete', label), onClick: () => setConfirming(true), children: t('rules.deleteShort') })
          ]
        }),
        below: warnings.length ? jsx('div', { className: `mt-1 ${ERROR_TEXT}`, children: warnings.join(' ') }) : undefined,
        description: `${matchSummary(rule)} · ${modeSummary(rule)}`,
        title: jsxs('span', {
          className: 'inline-flex flex-wrap items-center gap-2',
          children: [
            jsx('span', { children: `${index + 1}. ${label}` }),
            rule?.override === true ? jsx(Badge, { variant: 'outline', children: t('rules.override') }) : null,
            warnings.length ? jsx(Badge, { title: warnings.join(' '), variant: 'destructive', children: t('rules.invalid') }) : null
          ]
        })
      }),
      jsx(ConfirmDialog, {
        confirmLabel: t('common.remove'),
        description: t('rules.deleteBody'),
        destructive: true,
        dismissOnConfirm: true,
        onClose: () => setConfirming(false),
        onConfirm: () => app.deleteRule(index),
        open: confirming,
        title: t('rules.deleteTitle', label)
      })
    ]
  })
}

function RulesPage({ app }) {
  const state = useStore(app.store)
  const dirtyRules = state.saved && !sameJson(state.rules, state.saved.rules)
  const warnings = dirtyRules ? (state.lint?.rules === state.rules ? state.lint.warnings : []) : (state.status.data?.warnings ?? [])
  const { byRule, general } = splitWarnings(warnings)

  return jsx(PageFrame, {
    app,
    children: jsxs('div', {
      children: [
        jsxs('div', {
          className: 'flex items-center gap-3 py-2',
          children: [
            jsx('div', { className: `flex-1 ${CAPTION}`, children: t('rules.firstMatch') }),
            jsx(Button, { onClick: () => app.openEditor(null, { match: { base_url: '' }, price_as: 'auto' }), size: 'sm', type: 'button', children: t('rules.add') })
          ]
        }),
        general.length
          ? jsx('ul', { 'aria-label': t('rules.otherWarnings'), className: `list-disc pl-5 ${ERROR_TEXT}`, children: general.map((w, i) => jsx('li', { children: w }, i)) })
          : null,
        state.rules.length === 0
          ? jsx(EmptyState, { description: t('rules.emptyBody'), title: t('rules.emptyTitle') })
          : jsx('div', {
              'aria-label': t('page.rules'),
              role: 'list',
              children: state.rules.map((rule, index) =>
                jsx(RuleRow, { app, count: state.rules.length, index, rule, warnings: byRule[index] ?? [] }, `${index}:${JSON.stringify(rule)}`)
              )
            })
      ]
    })
  })
}

function AliasesPage({ app }) {
  const state = useStore(app.store)
  const rows = state.aliasRows
  const problems = aliasProblems(rows)
  const uid = useId()
  const update = (index, patch) => app.setAliasRows(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))

  return jsx(PageFrame, {
    app,
    children: jsxs('div', {
      className: 'grid gap-2',
      children: [
        jsx('div', { className: `py-2 ${CAPTION}`, children: t('aliases.intro') }),
        rows.length === 0 ? jsx(EmptyState, { description: t('aliases.emptyBody'), title: t('aliases.emptyTitle') }) : null,
        ...rows.map((row, index) =>
          jsxs('div', {
            className: 'grid gap-1',
            children: [
              jsxs('div', {
                className: 'grid grid-cols-[1fr_1fr_auto] items-center gap-2',
                children: [
                  jsx(Input, {
                    'aria-describedby': problems[index] ? `${uid}-${row.key}-error` : undefined,
                    'aria-invalid': problems[index] ? true : undefined,
                    'aria-label': t('aliases.from', index + 1),
                    onChange: event => update(index, { from: event.target.value }),
                    placeholder: 'chat-latest',
                    value: row.from
                  }),
                  jsx(Input, {
                    'aria-label': t('aliases.to', index + 1),
                    onChange: event => update(index, { to: event.target.value }),
                    placeholder: 'gpt-5.1',
                    value: row.to
                  }),
                  jsx(IconButton, { label: t('aliases.remove', index + 1), onClick: () => app.setAliasRows(rows.filter((_, i) => i !== index)), children: '✕' })
                ]
              }),
              problems[index] ? jsx('div', { className: ERROR_TEXT, id: `${uid}-${row.key}-error`, children: problems[index] }) : null
            ]
          }, row.key)
        ),
        jsx('div', { children: jsx(Button, { onClick: app.addAlias, size: 'sm', type: 'button', variant: 'secondary', children: t('aliases.add') }) })
      ]
    })
  })
}

function BackfillPage({ app }) {
  const state = useStore(app.store)
  const [confirming, setConfirming] = useState(false)
  const uid = useId()
  const { days, preview, result } = state.backfill
  const dirty = app.isDirty(state)
  const data = preview.data
  const canApply = !dirty && preview.state === 'ready' && (data?.rows_updated ?? 0) > 0
  const options = ['7', '30', '90', '365', 'all']

  return jsx(PageFrame, {
    app,
    children: jsxs('div', {
      className: 'grid gap-3',
      children: [
        jsx('div', { className: `py-2 ${CAPTION}`, children: t('backfill.intro') }),
        jsxs('div', {
          className: 'flex flex-wrap items-end gap-3',
          children: [
            jsx(Field, {
              id: `${uid}-days`,
              label: t('backfill.days'),
              children: jsxs(Select, {
                onValueChange: app.setBackfillDays,
                value: days,
                children: [
                  jsx(SelectTrigger, { 'aria-label': t('backfill.days'), id: `${uid}-days`, children: jsx(SelectValue, {}) }),
                  jsx(SelectContent, { children: options.map(o => jsx(SelectItem, { value: o, children: t(`backfill.days.${o}`) }, o)) })
                ]
              })
            }),
            jsx(Button, {
              disabled: dirty || preview.state === 'loading',
              onClick: app.backfillPreview,
              type: 'button',
              variant: 'secondary',
              children: preview.state === 'loading' ? t('common.loading') : t('backfill.preview')
            }),
            jsx(Button, { disabled: !canApply, onClick: () => setConfirming(true), type: 'button', variant: 'destructive', children: t('backfill.apply') })
          ]
        }),
        dirty
          ? jsx('div', { className: ERROR_TEXT, role: 'status', children: t('backfill.dirty') })
          : preview.state !== 'ready' && !result
            ? jsx('div', { className: CAPTION, children: t('backfill.needPreview') })
            : null,
        preview.state === 'error' ? jsx(ErrorPanel, { info: preview.error, onRetry: app.backfillPreview }) : null,
        preview.state === 'ready' && data ? jsx(BackfillSummary, { data }) : null,
        result
          ? jsxs('div', {
              className: 'grid gap-1',
              role: 'status',
              children: [
                jsx('div', { className: 'text-foreground', children: t('backfill.applied', result.rows_updated ?? 0, result.sessions_updated ?? 0) }),
                jsx('div', { className: CAPTION, children: result.backup_path ? t('backfill.backup', result.backup_path) : t('backfill.noBackup') })
              ]
            })
          : null,
        jsx(ConfirmDialog, {
          confirmLabel: t('backfill.confirm'),
          description: t('backfill.confirmBody', data?.rows_updated ?? 0, data?.sessions_updated ?? 0),
          destructive: true,
          onClose: () => setConfirming(false),
          onConfirm: app.backfillApply,
          open: confirming,
          title: t('backfill.confirmTitle')
        })
      ]
    })
  })
}

function BackfillSummary({ data }) {
  const endpoints = Array.isArray(data.by_endpoint) ? data.by_endpoint : []

  if (!data.rows_updated) {
    return jsx('div', { className: CAPTION, role: 'status', children: t('backfill.nothing') })
  }

  const cell = (children, key) => jsx('td', { className: 'py-1 pr-3 align-top', children }, key)
  const head = (children, key) => jsx('th', { className: 'py-1 pr-3 text-left font-medium', scope: 'col', children }, key)

  return jsxs('div', {
    className: 'grid gap-2',
    role: 'status',
    children: [
      jsx('div', { className: 'text-foreground', children: t('backfill.summary', data.rows_updated, data.sessions_updated ?? 0) }),
      jsx('div', { className: CAPTION, children: `${t('backfill.delta')}: ${formatUsd(data.delta_usd)} · ${t('backfill.considered')}: ${data.rows_considered ?? 0}` }),
      jsxs('table', {
        className: `w-full ${CAPTION}`,
        children: [
          jsx('caption', { className: 'text-left', children: t('backfill.table') }),
          jsx('thead', {
            children: jsx('tr', {
              children: [
                head(t('backfill.col.endpoint'), 'e'),
                head(t('backfill.col.rule'), 'r'),
                head(t('backfill.col.models'), 'm'),
                head(t('backfill.col.rows'), 'n'),
                head(t('backfill.col.delta'), 'd')
              ]
            })
          }),
          jsx('tbody', {
            children: endpoints.map((e, i) =>
              jsx('tr', {
                children: [
                  cell(e.endpoint, 'e'),
                  cell(e.matched_rule === null || e.matched_rule === undefined ? '—' : typeof e.matched_rule === 'number' ? t('rules.label', e.matched_rule + 1) : e.matched_rule, 'r'),
                  cell((e.models ?? []).join(', '), 'm'),
                  cell(String(e.rows ?? 0), 'n'),
                  cell(formatUsd(e.delta_usd), 'd')
                ]
              }, `${e.endpoint}|${i}`)
            )
          })
        ]
      })
    ]
  })
}

// ═════════════════════════════════════════════════════════════════════════════════════════════
// Registration
// ═════════════════════════════════════════════════════════════════════════════════════════════

export default {
  id: PLUGIN_ID,
  name: 'Gateway Pricing',
  // Opt-in: the desktop half stays off until the user switches it on in Capabilities -> Plugins.
  defaultEnabled: false,
  register(ctx) {
    // Translatable when the host supports plugin bundles; t() keeps reading STRINGS either way, and a
    // host that rejects the bundle must not take the whole page down with it.
    try {
      ctx.i18n?.register?.({ en: STRINGS })
    } catch {
      // English from STRINGS only.
    }

    const app = createController(ctx)

    ctx.onDispose?.(app.dispose)
    ctx.onEvent?.(SETTINGS_EVENT, () => {
      app.onSettingsChanged()
    })

    const unlisten = host?.state?.profile?.listen?.(() => app.onProfileChanged())

    if (typeof unlisten === 'function') {
      ctx.onDispose?.(unlisten)
    }

    const page = {
      id: 'settings',
      title: t('page.title'),
      icon: 'credit-card',
      render: () => jsx(OverviewPage, { app }),
      children: [
        { id: 'rules', title: t('page.rules'), render: () => jsx(RulesPage, { app }) },
        { id: 'aliases', title: t('page.aliases'), render: () => jsx(AliasesPage, { app }) },
        { id: 'backfill', title: t('page.backfill'), render: () => jsx(BackfillPage, { app }) }
      ]
    }

    if (typeof ctx.registerSettingsPage === 'function') {
      ctx.registerSettingsPage(page)
    } else {
      ctx.register?.({
        area: SETTINGS_PLUGINS_AREA,
        id: page.id,
        title: page.title,
        render: page.render,
        data: { icon: page.icon, children: page.children }
      })
    }
  }
}
