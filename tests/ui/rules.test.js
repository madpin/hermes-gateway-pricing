import { cleanup, fireEvent, screen, within } from '@testing-library/react'

import plugin from '../../desktop/plugin.js'

import { advance, deferred, fixture, flush, httpError, setup } from './fake-ctx.js'

afterEach(cleanup)

const settings = fixture('settings')
const rows = () => within(screen.getByRole('list', { name: 'Rules' })).getAllByRole('listitem')
const rowTitles = () =>
  rows().map(row => row.querySelector('[data-stub="list-row"] > div').textContent.replace(/override|invalid/g, ''))
const lastSaved = gateway => gateway.settingsCalls().at(-1).params.values.rules

async function save(gateway) {
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await flush()

  return lastSaved(gateway)
}

describe('Rules list', () => {
  it('lists the rules in order with match and mode summaries', async () => {
    const { show } = await setup(plugin)
    await show('rules')

    expect(screen.getByText('Rules are checked from top to bottom; the first rule that matches wins.')).toBeTruthy()
    expect(rowTitles()).toEqual(['1. claude-shim', '2. gemini-shim', '3. gateway', '4. llama'])
    expect(screen.getByText('127.0.0.1:9001 · all models · bills like anthropic')).toBeTruthy()
    expect(screen.getByText('gateway.example.com/openai · all models · bills like openai x1.1')).toBeTruthy()
    expect(screen.getByText('127.0.0.1:9003 · all models · free')).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Unsaved changes' })).toBeNull()
  })

  it('labels rules without an id by position and shows the override badge', async () => {
    const { show } = await setup(plugin, {
      values: { ...settings, rules: [{ match: { base_url: 'gateway.example.com', models: ['claude-*'] }, prices: { input: 3, output: 15 }, override: true }] }
    })
    await show('rules')

    expect(rowTitles()).toEqual(['1. Rule 1'])
    expect(within(rows()[0]).getByText('override')).toBeTruthy()
    expect(screen.getByText('gateway.example.com · claude-* · $3 / $15 per 1M')).toBeTruthy()
  })

  it('marks rules the backend rejects as invalid, with its warning', async () => {
    const { show } = await setup(plugin, {
      values: { ...settings, rules: [...settings.rules, { id: 'future', match: { base_url: 'future.example.com' }, price_as: 'auto', tier: 'gold' }] },
      ctxOptions: { routes: { 'GET /status': () => fixture('status-warnings') } }
    })
    await show('rules')
    const future = rows()[4]

    expect(within(future).getByText('invalid')).toBeTruthy()
    expect(within(future).getByText('rules[4] (future): unknown key(s) tier; rule skipped')).toBeTruthy()
    expect(within(rows()[0]).queryByText('invalid')).toBeNull()
  })

  it('asks the backend about draft rules and shows its warnings before saving', async () => {
    const { show, ctx } = await setup(plugin, {
      ctxOptions: { routes: { 'POST /preview': () => fixture('preview-invalid-draft') } }
    })
    await show('rules')
    fireEvent.click(screen.getByRole('button', { name: 'Duplicate llama' }))
    await advance(ctx, 300)
    const lint = ctx.restCalls.filter(c => c.route === '/preview').at(-1)

    expect(lint.body.rules).toHaveLength(5)
    expect(within(rows()[4]).getByText('invalid')).toBeTruthy()
  })

  it('toggles, reorders, duplicates and deletes in the draft, then saves the full ordered list', async () => {
    const { show, gateway } = await setup(plugin)
    await show('rules')

    fireEvent.click(screen.getByRole('switch', { name: 'Enable gemini-shim' }))
    await flush()
    expect(screen.getByRole('switch', { name: 'Enable gemini-shim' }).getAttribute('aria-checked')).toBe('false')

    expect(screen.getByRole('button', { name: 'Move claude-shim up' }).disabled).toBe(true)
    expect(screen.getByRole('button', { name: 'Move llama down' }).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Move llama up' }))
    await flush()
    expect(rowTitles()).toEqual(['1. claude-shim', '2. gemini-shim', '3. llama', '4. gateway'])

    fireEvent.click(screen.getByRole('button', { name: 'Duplicate claude-shim' }))
    await flush()
    expect(rowTitles()).toEqual(['1. claude-shim', '2. claude-shim-2', '3. gemini-shim', '4. llama', '5. gateway'])

    fireEvent.click(screen.getByRole('button', { name: 'Delete gateway' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete gateway?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await flush()
    expect(rowTitles()).toHaveLength(5)
    fireEvent.click(screen.getByRole('button', { name: 'Delete gateway' }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove' }))
    await flush()
    expect(rowTitles()).toEqual(['1. claude-shim', '2. claude-shim-2', '3. gemini-shim', '4. llama'])

    expect(await save(gateway)).toEqual([
      settings.rules[0],
      { ...settings.rules[0], id: 'claude-shim-2' },
      { ...settings.rules[1], enabled: false },
      settings.rules[3]
    ])
  })
})

describe('Rule editor', () => {
  it('edits a rule and keeps it in place', async () => {
    const { show, gateway } = await setup(plugin)
    await show('rules')
    fireEvent.click(screen.getByRole('button', { name: 'Edit gateway' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit rule' })

    expect(within(dialog).getByLabelText('Base URL').value).toBe('gateway.example.com/openai')
    expect(within(dialog).getByLabelText('Multiplier (optional)').value).toBe('1.1')
    expect(within(dialog).getByRole('combobox', { name: 'Vendor' }).textContent).toBe('openai')
    // The vendor list comes from /status (plus "auto").
    expect(within(dialog).getAllByRole('option').map(o => o.textContent)).toEqual(['auto (from the model id)', ...fixture('status').vendors])

    fireEvent.change(within(dialog).getByLabelText('Multiplier (optional)'), { target: { value: '1.25' } })
    fireEvent.change(within(dialog).getByLabelText('Models (optional)'), { target: { value: 'gpt-*,  chat-latest ,' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Done' }))
    await flush()

    expect(screen.queryByRole('dialog')).toBeNull()
    expect((await save(gateway))[2]).toEqual({
      id: 'gateway',
      match: { base_url: 'gateway.example.com/openai', models: ['gpt-*', 'chat-latest'] },
      price_as: 'openai',
      multiplier: 1.25
    })
  })

  it('adds a rule at the end in each mode', async () => {
    const { show, gateway } = await setup(plugin)
    await show('rules')

    fireEvent.click(screen.getByRole('button', { name: 'Add rule' }))
    let dialog = screen.getByRole('dialog', { name: 'Add rule' })
    expect(within(dialog).getByRole('button', { name: 'Done' }).disabled).toBe(true) // empty base URL
    fireEvent.change(within(dialog).getByLabelText('Base URL'), { target: { value: 'other.example.net' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Explicit prices' }))
    expect(within(dialog).getByText('USD per 1M tokens.')).toBeTruthy()
    fireEvent.change(within(dialog).getByLabelText('Input'), { target: { value: '3' } })
    fireEvent.change(within(dialog).getByLabelText('Output'), { target: { value: '-15' } })
    expect(within(dialog).getByText('Must be 0 or more.')).toBeTruthy()
    expect(within(dialog).getByRole('button', { name: 'Done' }).disabled).toBe(true)
    fireEvent.change(within(dialog).getByLabelText('Output'), { target: { value: '15' } })
    fireEvent.change(within(dialog).getByLabelText('Cache read (optional)'), { target: { value: '0.3' } })
    fireEvent.change(within(dialog).getByLabelText('Rule id (optional)'), { target: { value: 'flat' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Done' }))
    await flush()

    fireEvent.click(screen.getByRole('button', { name: 'Add rule' }))
    dialog = screen.getByRole('dialog', { name: 'Add rule' })
    fireEvent.change(within(dialog).getByLabelText('Base URL'), { target: { value: '127.0.0.1:9010' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Free' }))
    expect(within(dialog).getByText('Calls are recorded as included ($0).')).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('switch', { name: 'Override Hermes prices' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Done' }))
    await flush()

    fireEvent.click(screen.getByRole('button', { name: 'Add rule' }))
    dialog = screen.getByRole('dialog', { name: 'Add rule' })
    fireEvent.change(within(dialog).getByLabelText('Base URL'), { target: { value: '*.example.org' } })
    fireEvent.click(within(dialog).getByRole('option', { name: 'anthropic' }))
    fireEvent.change(within(dialog).getByLabelText('Vendor model (optional)'), { target: { value: 'claude-sonnet-4-5' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Done' }))
    await flush()

    expect((await save(gateway)).slice(4)).toEqual([
      { id: 'flat', match: { base_url: 'other.example.net' }, prices: { input: 3, output: 15, cache_read: 0.3 } },
      { match: { base_url: '127.0.0.1:9010' }, free: true, override: true },
      { match: { base_url: '*.example.org' }, price_as: 'anthropic/claude-sonnet-4-5' }
    ])
  })

  it.each([
    ['user@gateway.example.com', "Remove the 'user@' part."],
    ['gateway.example.com?x=1', 'Remove the query string or fragment.'],
    ['gateway.example.com:99999', 'The port must be a number from 0 to 65535.'],
    ['*.127.0.0.1', "'*.' cannot be used with an IP address."],
    ['foo*.example.com', "'*' is only allowed as a leading '*.' label."],
    ['a b', 'No spaces, control characters or backslashes.']
  ])('rejects base URL %s like the backend', async (value, message) => {
    const { show } = await setup(plugin)
    await show('rules')
    fireEvent.click(screen.getByRole('button', { name: 'Add rule' }))
    const input = screen.getByLabelText('Base URL')
    fireEvent.change(input, { target: { value } })

    expect(screen.getByText(message)).toBeTruthy()
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByRole('button', { name: 'Done' }).disabled).toBe(true)
  })

  it('refuses an id another rule already uses', async () => {
    const { show } = await setup(plugin)
    await show('rules')
    fireEvent.click(screen.getByRole('button', { name: 'Edit llama' }))
    fireEvent.change(screen.getByLabelText('Rule id (optional)'), { target: { value: 'gateway' } })

    expect(screen.getByText('Another rule already uses this id.')).toBeTruthy()
  })

  it('keeps unknown keys and untouched values through an edit', async () => {
    const future = { id: 'future', match: { base_url: 'future.example.com', region: 'eu' }, prices: { input: '3.0', output: 15, batch: 1 }, tier: 'gold' }
    const { show, gateway } = await setup(plugin, { values: { ...settings, rules: [future, { ...future, id: 'other' }] } })
    await show('rules')

    // Untouched: Done keeps the raw rule exactly, string prices included (draft stays clean).
    fireEvent.click(screen.getByRole('button', { name: 'Edit future' }))
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    await flush()
    expect(screen.queryByRole('region', { name: 'Unsaved changes' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Edit future' }))
    fireEvent.change(screen.getByLabelText('Cache write (optional)'), { target: { value: '3.75' } })
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    await flush()

    expect((await save(gateway))[0]).toEqual({
      id: 'future',
      match: { base_url: 'future.example.com', region: 'eu' },
      prices: { input: 3, output: 15, cache_write: 3.75, batch: 1 },
      tier: 'gold'
    })
  })

  it('cancel and Escape leave the draft untouched', async () => {
    const { show } = await setup(plugin)
    await show('rules')
    fireEvent.click(screen.getByRole('button', { name: 'Edit llama' }))
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'changed.example.com' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit llama' }))
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    await flush()

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.queryByRole('region', { name: 'Unsaved changes' })).toBeNull()
  })
})

describe('Live preview', () => {
  const previews = ctx => ctx.restCalls.filter(c => c.route === '/preview')

  it('debounces, posts the draft rules, model and base URL, and shows the result', async () => {
    const { show, ctx } = await setup(plugin)
    await show('rules')
    fireEvent.click(screen.getByRole('button', { name: 'Edit claude-shim' }))
    const model = screen.getByLabelText('Model to test')

    expect(screen.getByLabelText('Base URL to test').value).toBe('127.0.0.1:9001')
    expect(screen.getByText('Enter a model and a base URL to preview.')).toBeTruthy()
    for (const value of ['c', 'cl', 'claude-opus-5-5']) {
      fireEvent.change(model, { target: { value } })
      await advance(ctx, 100)
    }
    expect(previews(ctx)).toHaveLength(0)
    await advance(ctx, 300)
    expect(previews(ctx)).toHaveLength(1)
    expect(previews(ctx)[0].body).toEqual({
      model: 'claude-opus-5-5',
      base_url: '127.0.0.1:9001',
      rules: settings.rules,
      aliases: settings.aliases
    })

    const result = fixture('preview-price-as')
    expect(screen.getByText('claude-shim (this rule)')).toBeTruthy()
    expect(screen.getByText(result.status)).toBeTruthy()
    expect(screen.getByText(result.source)).toBeTruthy()
    expect(screen.getByText(`$${result.amount_usd.toFixed(2)}`)).toBeTruthy()
    expect(screen.getByText(new RegExp(`input \\$${result.per_million.input.toFixed(2)}`))).toBeTruthy()
  })

  it('previews the edited rule in place and says when an earlier rule wins', async () => {
    const { show, ctx } = await setup(plugin)
    await show('rules')
    fireEvent.click(screen.getByRole('button', { name: 'Edit llama' }))
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: '127.0.0.1:9001' } })
    fireEvent.change(screen.getByLabelText('Model to test'), { target: { value: 'claude-opus-5-5' } })
    await advance(ctx, 300)

    const body = previews(ctx).at(-1).body
    expect(body.rules[3]).toEqual({ id: 'llama', match: { base_url: '127.0.0.1:9001' }, free: true })
    expect(body.base_url).toBe('127.0.0.1:9001')
    expect(screen.getByText('claude-shim (an earlier rule wins)')).toBeTruthy()
  })

  it('ignores answers that arrive out of order', async () => {
    const { show, ctx } = await setup(plugin)
    const answers = []
    ctx.routes['POST /preview'] = () => {
      const d = deferred()
      answers.push(d)

      return d.promise
    }
    await show('rules')
    fireEvent.click(screen.getByRole('button', { name: 'Edit claude-shim' }))
    fireEvent.change(screen.getByLabelText('Model to test'), { target: { value: 'first' } })
    await advance(ctx, 300)
    fireEvent.change(screen.getByLabelText('Model to test'), { target: { value: 'second' } })
    await advance(ctx, 300)
    expect(answers).toHaveLength(2)

    answers[1].resolve({ ...fixture('preview-no-match'), reason: 'second answer' })
    await flush()
    answers[0].resolve({ ...fixture('preview-no-match'), reason: 'first answer' })
    await flush()

    expect(screen.getByText('second answer')).toBeTruthy()
    expect(screen.queryByText('first answer')).toBeNull()
  })

  it('shows the backend error text when the preview fails', async () => {
    const { show, ctx } = await setup(plugin)
    ctx.routes['POST /preview'] = () => {
      throw httpError(400, fixture('error-400').detail)
    }
    await show('rules')
    fireEvent.click(screen.getByRole('button', { name: 'Edit claude-shim' }))
    fireEvent.change(screen.getByLabelText('Model to test'), { target: { value: 'claude-opus-5-5' } })
    await advance(ctx, 300)

    expect(screen.getByRole('alert').textContent).toBe(`Preview failed: ${fixture('error-400').detail}`)
  })
})
