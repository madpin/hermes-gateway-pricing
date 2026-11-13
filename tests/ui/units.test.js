import {
  aliasProblems,
  classifyError,
  formToRule,
  matchSummary,
  modeSummary,
  previewDefaults,
  ruleFromDetected,
  ruleToForm,
  splitWarnings,
  validateBaseUrl,
  validateForm
} from '../../desktop/plugin.js'

import { fixture, httpError } from './fake-ctx.js'

describe('validateBaseUrl agrees with match.parse_pattern', () => {
  it.each(fixture('base-url-cases').map(c => [JSON.stringify(c.base_url), c.base_url, c.valid]))('%s', (_name, input, valid) => {
    const error = validateBaseUrl(input)

    expect(error === null, `backend says valid=${valid}, editor says ${error}`).toBe(valid)
  })
})

describe('classifyError', () => {
  it.each([
    ['IPC-wrapped bridge error', httpError(503, 'not active'), { kind: 'unavailable', status: 503, detail: 'not active' }],
    [
      'main-process httpStatusError',
      Object.assign(new Error('409: {"detail":"needs confirm=true"}'), { statusCode: 409 }),
      { kind: 'refused', status: 409, detail: 'needs confirm=true' }
    ],
    ['non-JSON body behind IPC', new Error("Error invoking remote method 'hermes:api': Error: 502: Bad Gateway"), { kind: 'generic', status: 502, detail: 'Bad Gateway' }],
    ['a 409 that mentions a timeout is still a refusal', httpError(409, 'busy_timeout expired: state.db is busy'), { kind: 'refused', status: 409 }],
    ['gateway timeout status', httpError(504, 'upstream'), { kind: 'timeout', status: 504 }],
    ['status property', Object.assign(new Error('boom'), { status: 404 }), { kind: 'not-mounted', status: 404, detail: 'boom' }],
    ['statusCode property', Object.assign(new Error('nope'), { statusCode: 409, detail: 'needs confirm' }), { kind: 'refused', status: 409, detail: 'needs confirm' }],
    ['response.status', Object.assign(new Error('x'), { response: { status: 400 } }), { kind: 'invalid', status: 400, detail: 'x' }],
    ['"HTTP 404" text', new Error('HTTP 404: Not Found'), { kind: 'not-mounted', status: 404, detail: 'Not Found' }],
    ['missing state.db', httpError(404, 'no state.db at /x/state.db'), { kind: 'no-db', status: 404 }],
    ['FastAPI 422 list detail', httpError(422, [{ msg: 'days must be >= 1' }]), { kind: 'invalid', status: 422, detail: 'days must be >= 1' }],
    ['timeout message', new Error('Request timed out after 15000ms'), { kind: 'timeout' }],
    ['TimeoutError name', Object.assign(new Error('slow'), { name: 'TimeoutError' }), { kind: 'timeout' }],
    ['gateway down', new Error('Hermes gateway unavailable'), { kind: 'offline', status: null }],
    ['a plain string', 'weird', { kind: 'generic', status: null, detail: 'weird' }]
  ])('%s', (_name, error, expected) => {
    expect(classifyError(error)).toMatchObject(expected)
  })
})

describe('rules <-> editor form', () => {
  it('round-trips unknown keys at every level and drops the other modes', () => {
    const raw = {
      id: 'gw',
      match: { base_url: 'gateway.example.com', models: 'claude-*', region: 'eu' },
      prices: { input: '3.0', output: 15, batch: 0.5 },
      tier: 'gold',
      override: false
    }
    const form = ruleToForm(raw)

    expect(form).toMatchObject({ baseUrl: 'gateway.example.com', models: 'claude-*', mode: 'prices', override: false })
    expect(form.prices).toEqual({ input: '3.0', output: '15', cache_read: '', cache_write: '' })
    expect(formToRule({ ...form, multiplier: '1.1' }, raw)).toEqual({
      id: 'gw',
      match: { base_url: 'gateway.example.com', models: ['claude-*'], region: 'eu' },
      prices: { input: 3, output: 15, batch: 0.5 },
      multiplier: 1.1,
      override: false,
      tier: 'gold'
    })
    expect(formToRule({ ...form, mode: 'free' }, raw)).toEqual({
      id: 'gw',
      match: { base_url: 'gateway.example.com', models: ['claude-*'], region: 'eu' },
      free: true,
      override: false,
      tier: 'gold'
    })
  })

  it('keeps vendor/model and alias spellings of price_as', () => {
    expect(ruleToForm({ match: { base_url: 'x.example.com' }, price_as: 'anthropic/claude-sonnet-4-5' })).toMatchObject({
      vendor: 'anthropic',
      vendorModel: 'claude-sonnet-4-5'
    })
    const alias = ruleToForm({ match: { base_url: 'x.example.com' }, price_as: 'claude' })

    expect(alias.vendor).toBe('claude')
    expect(formToRule(alias, {})).toEqual({ match: { base_url: 'x.example.com' }, price_as: 'claude' })
  })

  it('removes cleared optional fields instead of sending empty strings', () => {
    const raw = { match: { base_url: 'x.example.com' }, prices: { input: 1, output: 2, cache_read: 0.1 }, multiplier: 2 }
    const form = ruleToForm(raw)
    const rule = formToRule({ ...form, multiplier: '', prices: { ...form.prices, cache_read: '' } }, raw)

    expect(rule).toEqual({ match: { base_url: 'x.example.com' }, prices: { input: 1, output: 2 } })
  })

  it('validates like rules.py', () => {
    const base = ruleToForm({ match: { base_url: 'x.example.com' }, prices: {} })

    expect(validateForm(base)).toMatchObject({ 'prices.input': 'Required.', 'prices.output': 'Required.' })
    expect(validateForm({ ...base, prices: { input: '-1', output: 'abc', cache_read: '', cache_write: '' } })).toMatchObject({
      'prices.input': 'Must be 0 or more.',
      'prices.output': 'Enter a number.'
    })
    expect(validateForm({ ...base, mode: 'free', multiplier: '0' })).toEqual({ multiplier: 'Must be greater than 0.' })
    expect(validateForm({ ...base, mode: 'free', id: 'gw' }, ['gw'])).toEqual({ id: 'Another rule already uses this id.' })
    expect(validateForm({ ...base, mode: 'price_as', vendor: 'openai', vendorModel: 'openai/gpt-5' })).toHaveProperty('vendorModel')
    expect(validateForm({ ...base, mode: 'free' })).toEqual({})
  })

  it('summarises rules the way the list shows them', () => {
    const [shim, , gateway, llama] = fixture('settings').rules

    expect(`${matchSummary(shim)} · ${modeSummary(shim)}`).toBe('127.0.0.1:9001 · all models · bills like anthropic')
    expect(modeSummary(gateway)).toBe('bills like openai x1.1')
    expect(modeSummary(llama)).toBe('free')
    expect(modeSummary({ prices: { input: 3, output: 15, cache_read: 0.3 } })).toBe('$3 / $15 per 1M, cache read $0.3')
    expect(modeSummary({ price_as: 'auto' })).toBe('bills like the detected vendor')
  })

  it('derives preview defaults and rules from detected endpoints', () => {
    expect(previewDefaults(ruleToForm({ match: { base_url: '*.example.com', models: ['claude-*', 'gpt-5.1'] } }))).toEqual({
      baseUrl: 'api.example.com',
      model: 'gpt-5.1'
    })
    const [first] = fixture('detected')

    expect(ruleFromDetected(first)).toEqual({ match: { base_url: '127.0.0.1:9002/v1', models: ['gemini-3.8-flash'] }, price_as: 'google' })
  })

  it('splits backend warnings by rule and spots alias problems', () => {
    expect(splitWarnings(fixture('status-warnings').warnings)).toEqual({
      byRule: { 4: ['rules[4] (future): unknown key(s) tier; rule skipped'] },
      general: []
    })
    const problems = aliasProblems([
      { from: 'a', to: 'x' },
      { from: 'a', to: 'y' },
      { from: 'b', to: '' },
      { from: '', to: '' }
    ])

    expect(Object.keys(problems)).toEqual(['0', '1', '2'])
  })
})
