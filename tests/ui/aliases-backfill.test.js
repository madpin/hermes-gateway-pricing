import { cleanup, fireEvent, screen, within } from '@testing-library/react'

import plugin from '../../desktop/plugin.js'

import { fixture, flush, httpError, setup } from './fake-ctx.js'

afterEach(cleanup)

const settings = fixture('settings')

describe('Aliases', () => {
  it('edits, adds and removes aliases and saves them as a mapping', async () => {
    const { show, gateway } = await setup(plugin)
    await show('aliases')

    expect(screen.getByLabelText('Alias 1: model id').value).toBe('chat-latest')
    expect(screen.getByLabelText('Alias 1: price as').value).toBe('gpt-6.1-sol')
    fireEvent.change(screen.getByLabelText('Alias 1: price as'), { target: { value: 'gpt-6.2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add alias' }))
    await flush()
    // A blank new row is not a change yet, and not an error.
    fireEvent.change(screen.getByLabelText('Alias 2: model id'), { target: { value: ' house-model ' } })
    fireEvent.change(screen.getByLabelText('Alias 2: price as'), { target: { value: 'claude-sonnet-4-5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await flush()

    expect(gateway.settingsCalls().at(-1).params.values.aliases).toEqual({ 'chat-latest': 'gpt-6.2', 'house-model': 'claude-sonnet-4-5' })

    fireEvent.click(screen.getByRole('button', { name: 'Remove alias 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await flush()
    expect(gateway.settingsCalls().at(-1).params.values.aliases).toEqual({ 'house-model': 'claude-sonnet-4-5' })
  })

  it('flags duplicate and half-filled rows and blocks Save until fixed', async () => {
    const { show, gateway } = await setup(plugin)
    await show('aliases')
    fireEvent.click(screen.getByRole('button', { name: 'Add alias' }))
    await flush()
    fireEvent.change(screen.getByLabelText('Alias 2: model id'), { target: { value: 'chat-latest' } })
    fireEvent.change(screen.getByLabelText('Alias 2: price as'), { target: { value: 'gpt-5' } })
    await flush()

    expect(screen.getAllByText('"chat-latest" is listed more than once.')).toHaveLength(2)
    expect(screen.getByLabelText('Alias 1: model id').getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByText('Fix the alias errors before saving.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Save' }).disabled).toBe(true)

    fireEvent.change(screen.getByLabelText('Alias 2: model id'), { target: { value: 'chat-old' } })
    fireEvent.change(screen.getByLabelText('Alias 2: price as'), { target: { value: '' } })
    expect(screen.getByText('Fill in both sides, or remove the row.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Remove alias 2' }))
    await flush()

    expect(screen.queryByRole('region', { name: 'Unsaved changes' })).toBeNull()
    expect(gateway.settingsCalls()).toHaveLength(0)
  })

  it('shows an empty state without aliases', async () => {
    const { show } = await setup(plugin, { values: { ...settings, aliases: {} } })
    await show('aliases')

    expect(screen.getByText('No aliases')).toBeTruthy()
  })
})

describe('Backfill', () => {
  const backfillCalls = ctx => ctx.restCalls.filter(c => c.route === '/backfill')

  it('previews, then applies with confirm after the confirmation dialog', async () => {
    const { show, ctx } = await setup(plugin)
    await show('backfill')
    const apply = screen.getByRole('button', { name: 'Apply…' })

    expect(apply.disabled).toBe(true)
    expect(screen.getByText('Preview the changes first.')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Preview changes' }))
    await flush()
    const dry = fixture('backfill-dry-run')

    expect(backfillCalls(ctx)[0].body).toEqual({ dry_run: true, days: 30 })
    expect(screen.getByText(`${dry.rows_updated} usage rows in ${dry.sessions_updated} sessions would change.`)).toBeTruthy()
    expect(screen.getByText(new RegExp(`Cost added: \\$${dry.delta_usd.toFixed(2)}`))).toBeTruthy()
    const table = screen.getByRole('table', { name: 'Changes by endpoint' })
    const bodyRows = within(table).getAllByRole('row').slice(1)
    expect(bodyRows).toHaveLength(dry.by_endpoint.length)
    expect(within(bodyRows[0]).getByText(dry.by_endpoint[0].endpoint)).toBeTruthy()
    expect(within(bodyRows[0]).getByText(dry.by_endpoint[0].matched_rule)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Apply…' }))
    const dialog = screen.getByRole('dialog', { name: 'Apply backfill?' })
    expect(within(dialog).getByText(/A backup of state\.db is made first/)).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Back up and apply' }))
    await flush()

    const applied = fixture('backfill-applied')
    expect(backfillCalls(ctx)[1]).toMatchObject({ body: { dry_run: false, confirm: true, days: 30 } })
    expect(backfillCalls(ctx)[1].timeoutMs).toBeGreaterThan(backfillCalls(ctx)[0].timeoutMs)
    expect(backfillCalls(ctx).every(c => !('rules' in c.body))).toBe(true)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByText(`Updated ${applied.rows_updated} rows in ${applied.sessions_updated} sessions.`)).toBeTruthy()
    expect(screen.getByText(`Backup: ${applied.backup_path}`)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Apply…' }).disabled).toBe(true) // a new preview is needed
  })

  it('shows the backend refusal inside the dialog and keeps it open', async () => {
    const { show, ctx } = await setup(plugin)
    await show('backfill')
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes' }))
    await flush()
    ctx.routes['POST /backfill'] = () => {
      throw httpError(409, 'state.db kept changing while backing it up; nothing was written. Retry later.')
    }
    fireEvent.click(screen.getByRole('button', { name: 'Apply…' }))
    fireEvent.click(screen.getByRole('button', { name: 'Back up and apply' }))
    await flush()

    expect(within(screen.getByRole('dialog')).getByRole('alert').textContent).toBe(
      'Refused: state.db kept changing while backing it up; nothing was written. Retry later.'
    )
  })

  it('needs a fresh preview after the period changes, and omits days for all time', async () => {
    const { show, ctx } = await setup(plugin)
    await show('backfill')
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes' }))
    await flush()
    fireEvent.click(screen.getByRole('option', { name: 'All time' }))
    await flush()

    expect(screen.getByRole('button', { name: 'Apply…' }).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes' }))
    await flush()
    expect(backfillCalls(ctx).at(-1).body).toEqual({ dry_run: true })
  })

  it('is disabled with an explanation while there are unsaved changes', async () => {
    const { show, pageNode, gateway } = await setup(plugin)
    await show('overview')
    fireEvent.click(screen.getByRole('switch', { name: 'Apply pricing rules' }))
    cleanup()
    await show('backfill')

    expect(screen.getByText('Save or discard your changes first: backfill uses the saved rules.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Preview changes' }).disabled).toBe(true)
    expect(screen.getByRole('button', { name: 'Apply…' }).disabled).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await flush()
    expect(gateway.settingsCalls()).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Preview changes' }).disabled).toBe(false)
    expect(pageNode('backfill').title).toBe('Backfill')
  })

  it('reports a nothing-to-do preview and preview failures', async () => {
    const { show, ctx } = await setup(plugin)
    ctx.routes['POST /backfill'] = () => ({ ...fixture('backfill-dry-run'), rows_updated: 0, sessions_updated: 0, by_endpoint: [] })
    await show('backfill')
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes' }))
    await flush()
    expect(screen.getByText('Nothing to change.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Apply…' }).disabled).toBe(true)

    ctx.routes['POST /backfill'] = () => {
      throw httpError(503, 'gateway-pricing is not active in this process')
    }
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes' }))
    await flush()
    expect(screen.getByText('Gateway pricing is not active in the gateway')).toBeTruthy()
  })
})
