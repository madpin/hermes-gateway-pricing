import { cleanup, fireEvent, screen, within } from '@testing-library/react'

import plugin from '../../desktop/plugin.js'

import { advance, fixture, flush, httpError, setup } from './fake-ctx.js'

afterEach(cleanup)

describe('registration', () => {
  it('registers one settings page with the three sub-pages', async () => {
    const { ctx } = await setup(plugin)

    expect(ctx.pages).toHaveLength(1)
    expect(ctx.pages[0]).toMatchObject({ id: 'settings', title: 'Gateway Pricing' })
    expect(ctx.pages[0].children.map(c => [c.id, c.title])).toEqual([
      ['rules', 'Rules'],
      ['aliases', 'Aliases'],
      ['backfill', 'Backfill']
    ])
    expect(ctx.i18nBundles[0].en['page.title']).toBe('Gateway Pricing')
    // Nothing is fetched until a page is opened.
    expect(ctx.restCalls).toEqual([])
  })

  it('falls back to ctx.register on hosts without registerSettingsPage', async () => {
    const { ctx, show } = await setup(plugin, { ctxOptions: { settingsPage: false, i18n: false } })

    expect(ctx.contributions).toHaveLength(1)
    expect(ctx.contributions[0]).toMatchObject({ area: 'settings.plugins', id: 'settings', title: 'Gateway Pricing' })
    expect(ctx.contributions[0].data.children.map(c => c.id)).toEqual(['rules', 'aliases', 'backfill'])
    await show('overview')
    expect(screen.getByText('Active')).toBeTruthy()
  })
})

describe('Overview', () => {
  it('shows status, versions, rule counts and the profile', async () => {
    const { show, ctx } = await setup(plugin, { profile: 'work' })
    await show('overview')

    expect(screen.getByText('Active')).toBeTruthy()
    expect(screen.getByText(/Profile work · plugin 0\.2\.0 · Hermes 0\.0\.0-fixture/)).toBeTruthy()
    expect(screen.getByText(/4 of 4 rules valid/)).toBeTruthy()
    expect(ctx.restCalls.map(c => `${c.method} ${c.path}`)).toEqual(['GET /status', 'GET /detected?days=30'])
    expect(ctx.restCalls.every(c => c.timeoutMs > 0)).toBe(true)
  })

  it('lists warnings from the backend', async () => {
    const { show } = await setup(plugin, { ctxOptions: { routes: { 'GET /status': () => fixture('status-warnings') } } })
    await show('overview')

    expect(screen.getByText('rules[4] (future): unknown key(s) tier; rule skipped')).toBeTruthy()
    expect(screen.getByText(/4 of 5 rules valid/)).toBeTruthy()
  })

  it('shows the inactive reason and the next step', async () => {
    const inactive = fixture('status-inactive')
    const { show } = await setup(plugin, { ctxOptions: { routes: { 'GET /status': () => inactive } } })
    await show('overview')

    expect(screen.getByText(`Inactive: ${inactive.reason}`)).toBeTruthy()
    expect(screen.getByText(/listed in plugins\.enabled for this profile, then restart the gateway/)).toBeTruthy()
  })

  it('lists unpriced endpoints with their suggestion, and hides the priced ones', async () => {
    const { show } = await setup(plugin)
    await show('overview')
    const list = screen.getByRole('list')
    const items = within(list).getAllByRole('listitem')
    const unpriced = fixture('detected').filter(r => r.matched_rule === null)

    expect(items).toHaveLength(unpriced.length)
    expect(within(list).queryByText('Qwen3.6-35B-A3B-UD-Q4_K_M')).toBeNull()
    expect(screen.getByText(/1 recorded endpoint already match a rule/)).toBeTruthy()
    const gemini = items.find(item => within(item).queryByText('gemini-3.8-flash'))
    expect(within(gemini).getByText(/127\.0\.0\.1:9002 · 1 session · 80\.9k tokens/)).toBeTruthy()
    expect(within(gemini).getByText(/Suggestion: bills like google \(high confidence\)/)).toBeTruthy()
    const mystery = items.find(item => within(item).queryByText('mystery-model'))
    expect(within(mystery).getByText(/No suggestion/)).toBeTruthy()
  })

  it('shows an empty state when nothing is unpriced', async () => {
    const { show } = await setup(plugin, { ctxOptions: { routes: { 'GET /detected': () => fixture('detected-all-priced') } } })
    await show('overview')

    expect(screen.getByText('Nothing unpriced')).toBeTruthy()
  })

  it('pre-fills the rule editor from a suggestion', async () => {
    const { show, ctx } = await setup(plugin)
    await show('overview')
    fireEvent.click(screen.getByRole('button', { name: 'Add rule for gemini-3.8-flash' }))
    const dialog = screen.getByRole('dialog', { name: 'Add rule' })

    expect(within(dialog).getByLabelText('Base URL').value).toBe('127.0.0.1:9002/v1')
    expect(within(dialog).getByLabelText('Models (optional)').value).toBe('gemini-3.8-flash')
    expect(within(dialog).getByRole('button', { name: 'Bill like a vendor' }).getAttribute('aria-pressed')).toBe('true')
    expect(within(dialog).getByRole('combobox', { name: 'Vendor' }).textContent).toBe('google')
    // The live preview defaults to the rule's own host and model.
    await advance(ctx, 300)
    const preview = ctx.restCalls.filter(c => c.route === '/preview').at(-1)
    expect(preview.body.model).toBe('gemini-3.8-flash')
    expect(preview.body.base_url).toBe('127.0.0.1:9002/v1')
    expect(preview.body.rules.at(-1)).toEqual({ match: { base_url: '127.0.0.1:9002/v1', models: ['gemini-3.8-flash'] }, price_as: 'google' })
  })

  it('pre-fills explicit prices when there is no suggestion', async () => {
    const { show } = await setup(plugin)
    await show('overview')
    fireEvent.click(screen.getByRole('button', { name: 'Add rule for mystery-model' }))
    const dialog = screen.getByRole('dialog')

    expect(within(dialog).getByLabelText('Base URL').value).toBe('other.example.net/v1')
    expect(within(dialog).getByRole('button', { name: 'Explicit prices' }).getAttribute('aria-pressed')).toBe('true')
    expect(within(dialog).getByText('Fix the highlighted fields first.')).toBeTruthy()
    expect(within(dialog).getByRole('button', { name: 'Done' }).disabled).toBe(true)
  })

  it('edits the master switch in the draft', async () => {
    const { show } = await setup(plugin)
    await show('overview')
    const toggle = screen.getByRole('switch', { name: 'Apply pricing rules' })

    expect(toggle.getAttribute('aria-checked')).toBe('true')
    fireEvent.click(toggle)
    await flush()
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    expect(screen.getByRole('region', { name: 'Unsaved changes' })).toBeTruthy()
  })
})

describe('Overview failure states', () => {
  it.each([
    [httpError(404, 'Not Found'), 'The gateway-pricing API is not available', /restart the gateway so its API mounts/],
    [httpError(503, 'pricing wrap is not installed in this process'), 'Gateway pricing is not active in the gateway', /pricing wrap is not installed/],
    [new Error('Request timed out after 15000ms'), 'Hermes did not answer in time', /Check that the gateway is running/],
    [Object.assign(new Error('Service Unavailable'), { status: 503 }), 'Gateway pricing is not active in the gateway', /Restart the gateway/]
  ])('status failure %#', async (error, title, body) => {
    const { show, ctx } = await setup(plugin, {
      ctxOptions: {
        routes: {
          'GET /status': () => {
            throw error
          },
          'GET /detected': () => {
            throw error
          }
        }
      }
    })
    await show('overview')

    expect(screen.getAllByText(title).length).toBeGreaterThan(0)
    expect(screen.getAllByText(body).length).toBeGreaterThan(0)
    // The master switch still works: the page is never blank.
    expect(screen.getByRole('switch', { name: 'Apply pricing rules' })).toBeTruthy()

    ctx.routes['GET /status'] = () => fixture('status')
    fireEvent.click(screen.getAllByRole('button', { name: 'Retry' })[0])
    await flush()
    expect(screen.getByText('Active')).toBeTruthy()
  })

  it('treats a missing state.db as "nothing recorded yet", not an error', async () => {
    const { show } = await setup(plugin, {
      ctxOptions: {
        routes: {
          'GET /detected': () => {
            throw httpError(404, 'no state.db at <home>/state.db')
          }
        }
      }
    })
    await show('overview')

    expect(screen.getByText('No sessions recorded yet')).toBeTruthy()
  })

  it('explains when the settings cannot be read', async () => {
    const { show, gateway } = await setup(plugin)
    gateway.onList = () => ({ plugins: [] })
    await show('overview')

    expect(screen.getByText('The gateway-pricing agent plugin is not installed for this profile')).toBeTruthy()
    gateway.onList = null
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await flush()
    expect(screen.getByRole('switch', { name: 'Apply pricing rules' })).toBeTruthy()
  })

  it('explains when Hermes is not connected', async () => {
    const { show, gateway } = await setup(plugin)
    gateway.onList = () => {
      throw new Error('Hermes gateway unavailable')
    }
    await show('overview')

    expect(screen.getByText('Not connected to Hermes')).toBeTruthy()
  })
})
