import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createElement } from 'react'

import plugin from '../../desktop/plugin.js'

import { advance, fixture, flush, setup } from './fake-ctx.js'

afterEach(cleanup)

const settings = fixture('settings')
const EVENT = 'plugin.gateway-pricing.settings.changed'
const bar = () => screen.queryByRole('region', { name: 'Unsaved changes' })
const masterSwitch = () => screen.getByRole('switch', { name: 'Apply pricing rules' })

async function makeDirty() {
  fireEvent.click(masterSwitch())
  await flush()
}

describe('Draft, Save and Discard', () => {
  it('saves through plugins.manage with exactly the documented payload', async () => {
    const { show, gateway, ctx } = await setup(plugin)
    await show('overview')
    expect(bar()).toBeNull()
    await makeDirty()
    expect(bar()).toBeTruthy()

    const statusCalls = ctx.restCalls.filter(c => c.route === '/status').length
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await flush()

    expect(gateway.settingsCalls().map(c => c.params)).toEqual([
      { action: 'settings', key: 'gateway-pricing', values: { rules: settings.rules, aliases: settings.aliases, enabled: false } }
    ])
    expect(gateway.calls[0].params).toEqual({ action: 'list' })
    expect(bar()).toBeNull()
    expect(masterSwitch().getAttribute('aria-checked')).toBe('false')
    // Status and detection are re-read after a save.
    expect(ctx.restCalls.filter(c => c.route === '/status').length).toBe(statusCalls + 1)
  })

  it('keeps the draft and explains when Hermes refuses to write', async () => {
    const { show, gateway } = await setup(plugin)
    gateway.onSettings = () => {
      throw new Error('managed install: config.yaml is read-only (PermissionError)')
    }
    await show('overview')
    await makeDirty()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await flush()

    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('Hermes did not save the changes: managed install: config.yaml is read-only (PermissionError)')
    expect(alert.textContent).toContain('managed install')
    expect(bar()).toBeTruthy()
    expect(masterSwitch().getAttribute('aria-checked')).toBe('false')

    gateway.onSettings = () => ({ ok: false, error: "'rules' should be json (got str)" })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await flush()
    expect(screen.getByRole('alert').textContent).toContain("'rules' should be json (got str)")

    gateway.onSettings = null
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await flush()
    expect(bar()).toBeNull()
    expect(gateway.values.enabled).toBe(false)
  })

  it('reports a save timeout', async () => {
    const { show, gateway } = await setup(plugin)
    gateway.onSettings = () => {
      throw new Error('Request timed out')
    }
    await show('overview')
    await makeDirty()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await flush()

    expect(screen.getByRole('alert').textContent).toContain('Hermes did not answer in time')
  })

  it('discards back to the saved values', async () => {
    const { show, gateway } = await setup(plugin)
    await show('overview')
    await makeDirty()
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    await flush()

    expect(bar()).toBeNull()
    expect(masterSwitch().getAttribute('aria-checked')).toBe('true')
    expect(gateway.settingsCalls()).toHaveLength(0)
  })

  it('shares one draft across the sub-pages', async () => {
    const { show, pageNode } = await setup(plugin)
    await show('overview')
    await makeDirty()
    cleanup()
    render(createElement(() => pageNode('rules').render()))
    await flush()

    expect(bar()).toBeTruthy()
  })
})

describe('settings changed elsewhere', () => {
  it('reloads silently while the draft is clean', async () => {
    const { show, gateway, ctx } = await setup(plugin)
    await show('rules')
    gateway.values = { ...gateway.values, rules: [settings.rules[3]] }
    ctx.emit(EVENT, { written: ['rules'] })
    await flush()

    expect(screen.queryByText('claude-shim')).toBeNull()
    expect(screen.getByText('1. llama')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('offers Reload / Keep mine while the draft is dirty', async () => {
    const { show, gateway, ctx } = await setup(plugin)
    await show('overview')
    await makeDirty()
    gateway.values = { ...gateway.values, aliases: { 'chat-latest': 'gpt-6.2' } }
    ctx.emit(EVENT, {})
    await flush()

    expect(screen.getByRole('alert').textContent).toContain('changed elsewhere')
    expect(masterSwitch().getAttribute('aria-checked')).toBe('false')

    fireEvent.click(screen.getByRole('button', { name: 'Keep mine' }))
    await flush()
    expect(screen.queryByRole('alert')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await flush()
    // "Keep mine" saves the draft as a whole: the other change is overwritten knowingly.
    expect(gateway.settingsCalls().at(-1).params.values.aliases).toEqual(settings.aliases)
  })

  it('Reload adopts the other change and drops the draft', async () => {
    const { show, gateway, ctx } = await setup(plugin)
    await show('overview')
    await makeDirty()
    gateway.values = { ...gateway.values, enabled: true, rules: [] }
    ctx.emit(EVENT, {})
    await flush()
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }))
    await flush()

    expect(bar()).toBeNull()
    expect(masterSwitch().getAttribute('aria-checked')).toBe('true')
  })

  it('does not flag its own save as a change made elsewhere', async () => {
    const { show, gateway, ctx } = await setup(plugin)
    gateway.onSettings = () => {
      ctx.emit(EVENT, {}) // Hermes broadcasts before the RPC answers
    }
    await show('overview')
    await makeDirty()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await flush()
    ctx.emit(EVENT, {}) // ...or after
    await flush()

    expect(screen.queryByRole('alert')).toBeNull()
    expect(bar()).toBeNull()
  })
})

describe('profile scoping', () => {
  it('reads and writes the profile ctx.rest targets', async () => {
    const { show, gateway } = await setup(plugin, { profile: 'work' })
    await show('overview')
    await makeDirty()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await flush()

    expect(gateway.calls[0].params).toEqual({ action: 'list', profile: 'work' })
    expect(gateway.settingsCalls()[0].params).toEqual({
      action: 'settings',
      key: 'gateway-pricing',
      values: { rules: settings.rules, aliases: settings.aliases, enabled: false },
      profile: 'work'
    })
  })

  it('reloads on a profile switch, or keeps the draft bound to its profile', async () => {
    const { show, gateway, host } = await setup(plugin, { profile: 'work' })
    await show('overview')
    host.state.profile._set('home')
    await flush()
    expect(gateway.calls.at(-1).params).toEqual({ action: 'list', profile: 'home' })
    expect(screen.getByText(/Profile home/)).toBeTruthy()

    await makeDirty()
    host.state.profile._set('work')
    await flush()
    expect(screen.getByRole('alert').textContent).toContain('Your unsaved edits belong to home')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await flush()
    expect(gateway.settingsCalls()[0].params.profile).toBe('home')
    expect(gateway.calls.at(-1).params).toEqual({ action: 'list', profile: 'work' })
  })
})

describe('dispose', () => {
  it('leaves no timers, event listeners or profile subscriptions behind', async () => {
    const { show, ctx, host } = await setup(plugin)
    await show('rules')
    expect(ctx.listenerCount(EVENT)).toBe(1)
    expect(host.state.profile._listeners.size).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: 'Duplicate llama' })) // schedules the draft lint
    await flush()
    expect(ctx.pendingTimers()).toBeGreaterThan(0)

    ctx.dispose()
    expect(ctx.pendingTimers()).toBe(0)
    expect(ctx.listenerCount(EVENT)).toBe(0)
    expect(host.state.profile._listeners.size).toBe(0)
    const calls = ctx.restCalls.length
    await advance(ctx, 1000)
    expect(ctx.restCalls.length).toBe(calls)
  })
})
