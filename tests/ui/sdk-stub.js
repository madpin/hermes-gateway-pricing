// A lightweight stand-in for `@hermes/plugin-sdk`, aliased in by vitest.config.mjs.
//
// Each component renders simple, accessible DOM so tests can query by role and label. The
// components the settings pages lean on REFUSE props the real component does not accept (and
// require the ones it needs): a typo in plugin.js fails a test instead of silently rendering
// nothing in Hermes. The prop lists come from the real sources (apps/desktop/src/app/settings/
// primitives.tsx for ListRow / ToggleRow, components/ui/* for the rest); stub-contract.test.js
// re-parses the real ListRow / ToggleRow signatures when they are available and fails on drift.
import { createContext, createElement as h, useContext, useEffect, useId, useState } from 'react'

export const STUB_PROPS = {
  ListRow: { allowed: ['title', 'description', 'hint', 'action', 'below', 'data-tour', 'id', 'wide', 'className'], required: ['title'] },
  ToggleRow: {
    allowed: ['checked', 'below', 'data-tour', 'description', 'disabled', 'hint', 'id', 'label', 'onChange', 'wide'],
    required: ['checked', 'label', 'onChange']
  },
  SegmentedControl: { allowed: ['options', 'value', 'onChange', 'className', 'disabled', 'sizing'], required: ['options', 'value', 'onChange'] },
  EmptyState: { allowed: ['title', 'description', 'className'], required: ['title'] },
  ErrorState: { allowed: ['children', 'className', 'description', 'icon', 'title'], required: ['title'] },
  ConfirmDialog: {
    allowed: ['open', 'onClose', 'onConfirm', 'title', 'description', 'children', 'confirmLabel', 'busyLabel', 'doneLabel', 'cancelLabel', 'destructive', 'dismissOnConfirm', 'secondaryAction'],
    required: ['open', 'onClose', 'onConfirm', 'title']
  },
  Switch: { allowed: ['checked', 'onCheckedChange', 'disabled', 'aria-label', 'aria-labelledby', 'id', 'className'], required: ['onCheckedChange'] },
  StatusDot: { allowed: ['tone', 'className', 'title', 'aria-label'], required: ['tone'] }
}

function checkProps(name, props) {
  const { allowed, required } = STUB_PROPS[name]

  for (const key of Object.keys(props)) {
    if (!allowed.includes(key)) {
      throw new Error(`[sdk-stub] ${name} does not accept prop "${key}"`)
    }
  }

  for (const key of required) {
    if (props[key] === undefined) {
      throw new Error(`[sdk-stub] ${name} requires prop "${key}"`)
    }
  }
}

// ── host ──────────────────────────────────────────────────────────────────────────────────────

function readonlyAtom(initial) {
  let value = initial
  const listeners = new Set()

  return {
    get: () => value,
    listen(fn) {
      listeners.add(fn)

      return () => listeners.delete(fn)
    },
    subscribe(fn) {
      fn(value)

      return this.listen(fn)
    },
    // test-only
    _set(next) {
      value = next
      listeners.forEach(fn => fn(next))
    },
    _listeners: listeners
  }
}

export const host = {
  request: async method => {
    throw new Error(`host.request(${method}) is not mocked in this test`)
  },
  state: { profile: readonlyAtom('') },
  notify: () => undefined,
  notifyError: () => undefined,
  onEvent: () => () => undefined
}

export function resetHost() {
  host.request = async method => {
    throw new Error(`host.request(${method}) is not mocked in this test`)
  }
  host.state.profile = readonlyAtom('')
}

// ── helpers ───────────────────────────────────────────────────────────────────────────────────

export const SETTINGS_PLUGINS_AREA = 'settings.plugins'

export function cn(...parts) {
  return parts.flat(Infinity).filter(Boolean).join(' ')
}

// ── UI kit ────────────────────────────────────────────────────────────────────────────────────

export function Button({ variant, size, asChild, className, type = 'button', ...rest }) {
  return h('button', { ...rest, 'data-variant': variant, type })
}

export function Input({ className, ...rest }) {
  return h('input', rest)
}

export function Badge({ variant, size, asChild, className, ...rest }) {
  return h('span', { ...rest, 'data-variant': variant })
}

export function StatusDot(props) {
  checkProps('StatusDot', props)

  return h('span', { 'aria-hidden': true, 'data-tone': props.tone, title: props.title })
}

export function Switch(props) {
  checkProps('Switch', props)
  const { checked, onCheckedChange, disabled, id } = props

  return h('button', {
    'aria-checked': Boolean(checked),
    'aria-label': props['aria-label'],
    'aria-labelledby': props['aria-labelledby'],
    disabled,
    id,
    onClick: () => onCheckedChange(!checked),
    role: 'switch',
    type: 'button'
  })
}

export function Tip({ children }) {
  return children
}

export function EmptyState(props) {
  checkProps('EmptyState', props)

  return h('div', { 'data-stub': 'empty-state' }, h('div', null, props.title), props.description ? h('div', null, props.description) : null)
}

export function ErrorState(props) {
  checkProps('ErrorState', props)
  const { title, description, children } = props

  return h(
    'div',
    { 'data-stub': 'error-state' },
    typeof title === 'string' ? h('h2', null, title) : title,
    typeof description === 'string' ? h('p', null, description) : description,
    children ? h('div', null, children) : null
  )
}

export function ListRow(props) {
  checkProps('ListRow', props)
  const { title, description, hint, action, below, id } = props

  return h(
    'div',
    { 'data-stub': 'list-row', id },
    h('div', null, title),
    description ? h('div', null, description) : null,
    hint ? h('div', null, hint) : null,
    below ?? null,
    action ? h('div', null, action) : null
  )
}

export function ToggleRow(props) {
  checkProps('ToggleRow', props)
  const { checked, below, description, disabled, hint, id, label, onChange, wide } = props

  return h(ListRow, {
    action: h(Switch, { 'aria-label': label, checked, disabled, onCheckedChange: onChange }),
    below,
    description,
    hint,
    id,
    title: label,
    wide
  })
}

export function SegmentedControl(props) {
  checkProps('SegmentedControl', props)
  const { options, value, onChange, disabled } = props

  return h(
    'div',
    null,
    options.map(option =>
      h('button', { 'aria-pressed': value === option.id, disabled, key: option.id, onClick: () => onChange(option.id), type: 'button' }, option.label)
    )
  )
}

// Select: always rendered open as a combobox + listbox of options (no portal, no popover).
const SelectContext = createContext(null)

export function Select({ value, onValueChange, disabled, children }) {
  return h(SelectContext.Provider, { value: { value, onValueChange, disabled } }, h('div', { 'data-stub': 'select' }, children))
}

export function SelectTrigger({ id, children, className, size, ...rest }) {
  const select = useContext(SelectContext)

  return h('button', { 'aria-label': rest['aria-label'], disabled: select.disabled, id, role: 'combobox', type: 'button' }, children)
}

export function SelectValue({ placeholder }) {
  const select = useContext(SelectContext)

  return h('span', null, select.value ?? placeholder ?? '')
}

export function SelectContent({ children }) {
  return h('div', { role: 'listbox' }, children)
}

export function SelectItem({ value, children, disabled }) {
  const select = useContext(SelectContext)

  return h(
    'div',
    {
      'aria-disabled': disabled || undefined,
      'aria-selected': select.value === value,
      onClick: () => !disabled && select.onValueChange?.(value),
      role: 'option'
    },
    children
  )
}

// Dialog: rendered inline while open; Escape asks to close, like Radix.
const DialogContext = createContext(null)

export function Dialog({ open, onOpenChange, children }) {
  const titleId = useId()

  return open ? h(DialogContext.Provider, { value: { onOpenChange, titleId } }, children) : null
}

export function DialogContent({ children, className, onKeyDown }) {
  const dialog = useContext(DialogContext)

  return h(
    'div',
    {
      'aria-labelledby': dialog.titleId,
      'aria-modal': true,
      onKeyDown: event => {
        onKeyDown?.(event)

        if (event.key === 'Escape') {
          dialog.onOpenChange?.(false)
        }
      },
      role: 'dialog'
    },
    children
  )
}

export function DialogHeader({ children }) {
  return h('div', null, children)
}

export function DialogFooter({ children }) {
  return h('div', null, children)
}

export function DialogTitle({ children }) {
  const dialog = useContext(DialogContext)

  return h('h2', { id: dialog?.titleId }, children)
}

export function DialogDescription({ children }) {
  return h('p', null, children)
}

// ConfirmDialog: same contract as the real one: onConfirm may throw to show an inline error and
// keep the dialog open; otherwise it closes (immediately here, after a 600 ms "done" beat there).
export function ConfirmDialog(props) {
  checkProps('ConfirmDialog', props)
  const { open, onClose, onConfirm, title, description, children, confirmLabel = 'Confirm', cancelLabel = 'Cancel', destructive } = props
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open) {
      setError(null)
      setBusy(false)
    }
  }, [open])

  if (!open) {
    return null
  }

  const run = async () => {
    setError(null)
    setBusy(true)

    try {
      await onConfirm()
      setBusy(false)
      onClose()
    } catch (err) {
      setBusy(false)
      setError(err instanceof Error ? err.message : 'Something went wrong')
    }
  }

  return h(
    Dialog,
    { onOpenChange: value => !value && !busy && onClose(), open: true },
    h(
      DialogContent,
      null,
      h(DialogTitle, null, title),
      description ? h(DialogDescription, null, description) : null,
      children ?? null,
      error ? h('div', { role: 'alert' }, error) : null,
      h(Button, { disabled: busy, onClick: onClose }, cancelLabel),
      h(Button, { disabled: busy, onClick: () => void run(), variant: destructive ? 'destructive' : 'default' }, confirmLabel)
    )
  )
}
