# Contributing

Thanks for helping! Bug reports with `hermes gateway-pricing status --json` output and the
(anonymised) rule that misbehaves are the most useful thing you can send.

## Running the tests

The tests need a Hermes checkout to import `agent.usage_pricing` from, plus `pytest`, `fastapi`
and `httpx`. Point `HERMES_SRC` at any Hermes checkout:

```bash
git clone --depth 1 https://github.com/NousResearch/hermes-agent /tmp/hermes-agent
HERMES_SRC=/tmp/hermes-agent python -m pytest -q
HERMES_SRC=/tmp/hermes-agent python scripts/check_hermes_compat.py
```

Or run with the Python of an existing Hermes installation, which already has Hermes importable.
Never install anything into it.

The tests protect your own Hermes data:

* Every test gets its own temporary `HERMES_HOME`. A guard fails a test if Hermes would resolve
  any other home.
* Network access is blocked.
* Backfill tests run on a synthetic database built by `tests/helpers.py`. When the local-only
  anonymised fixture `.ref/sessions-sanitized.db` exists, extra tests run on a copy of it;
  otherwise they are skipped.

Lint and format:

```bash
uvx ruff check .
uvx ruff format --check .
```

### Desktop UI tests (`tests/ui/`)

`desktop/plugin.js` is tested with vitest, jsdom and Testing Library against `tests/ui/sdk-stub.js`,
a small stand-in for `@hermes/plugin-sdk`. Node 20 or newer:

```bash
cd tests/ui
npm install          # devDependencies only, pinned in package.json; creates tests/ui/node_modules
npx vitest run
```

Two optional environment variables enable checks against the real SDK. Without them, those tests
are reported as skipped, with the reason in the test name:

* `HERMES_SDK_INDEX=/path/to/hermes-agent/apps/desktop/src/sdk/index.ts`: every name the plugin
  imports from the SDK must be exported there.
* `HERMES_DESKTOP_SRC=/path/to/hermes-agent/apps/desktop/src`: the stub's props for `ListRow`,
  `ToggleRow`, `SegmentedControl`, `ConfirmDialog`, `EmptyState` and `ErrorState` must equal the
  real components' props.

If you already have a Hermes desktop checkout with its `node_modules`, you can skip `npm install`.
Set `DESKTOP_NM=/path/to/apps/desktop/node_modules` and run that checkout's vitest with
`--root tests/ui`. The config then resolves react, react-dom and Testing Library from there, read
only.

The UI tests use JSON responses captured from the real backend, in `tests/ui/fixtures/`.

* After changing a response shape, regenerate them with
  `python tests/ui/gen_fixtures.py`. It runs in a temporary Hermes home on a synthetic database.
* `tests/test_ui_fixtures.py` (part of `pytest`) fails when the committed files drift from the
  backend.

CI runs the `ui` job with both variables set from a shallow clone of `NousResearch/hermes-agent`.

Rules for UI changes:

* **Plain ESM only.** Use `jsx()` / `jsxs()` from `react/jsx-runtime`, never JSX syntax. Import only
  from `@hermes/plugin-sdk`, `react` and `react/jsx-runtime`. No top-level `await`.
* **No raw timers or listeners.** Use `ctx.setTimeout`, `ctx.onEvent` and `ctx.onDispose`.
  `tests/ui/static.test.js` enforces this.
* **Theme variables and SDK components only.** No hardcoded colours.
* **All user-facing text goes in the `STRINGS` table**, read with `t()`.
* **When you add a prop to an SDK component call, add it to the stub's allow-list** in
  `sdk-stub.js`. The stub throws on props the real component does not accept.

## Code layout

| module | role | Hermes imports? |
|---|---|---|
| `__init__.py` | `register(ctx)`: install the wrap, register the CLI. Nothing else. | no (lazy, inside `register`) |
| `hermes_compat.py` | **the only module that touches Hermes internals**: contract check, wrap/unwrap, settings cache, evaluation | yes |
| `match.py` | URL parsing and endpoint patterns | no |
| `vendors.py` | vendor inference, model-id normalisation | no |
| `rules.py` | rule parsing/validation, `RuleSet.decide` | no |
| `pricing.py` | value types (`Usage`, `Priced`) | no |
| `statedb.py`, `detect.py`, `backfill.py` | schema-checked `state.db` access, detection, backfill | no (sqlite3 only) |
| `service.py`, `cli_commands.py`, `errors.py` | shared operations for the CLI and the API | via `hermes_compat` only |
| `dashboard/plugin_api.py` | FastAPI router mounted at `/api/plugins/<id>/` | no |
| `scripts/check_hermes_compat.py` | readable compatibility report (CI, troubleshooting) | via `hermes_compat` only |
| `desktop/plugin.js` | Hermes Desktop settings page (Settings → Plugins → Gateway Pricing): one plain-ESM file in sections (strings, helpers, rule model and validation, state and backend, shared UI, rule editor, pages, registration) | no (SDK + REST only) |
| `tests/ui/` | UI tests: `sdk-stub.js` (SDK stand-in), `fake-ctx.js` (fake `ctx`, fake `plugins.manage`, manual clock), `*.test.js`, `fixtures/` + `gen_fixtures.py` (real backend responses) | `gen_fixtures.py` only |

Rules for changes:

* **Only `hermes_compat.py` may touch Hermes internals.** Keep the runtime rebind there, plainly
  written. Never hide it from `hermes plugins validate`.
* **Keep the pure modules pure.** `tests/test_api_cli.py::test_pure_modules_have_no_hermes_imports`
  enforces this.
* **No module or directory named after a Hermes top-level package** (`cli`, `utils`, `tools`,
  `agent`, `providers`, `plugins`, ...). It would shadow the validator's view of core.
* **No new runtime dependencies.** Use the stdlib plus what Hermes ships.
* **Keep the hot path free of I/O and INFO logging.** Run the benchmark test after changing
  `rules.py`, `match.py` or the wrappers.
* **Any change to backfill needs a test** on the synthetic database.
* **Bump the version** in `plugin.yaml`, `version.py`, `__init__.py` and
  `dashboard/manifest.json`, add a `CHANGELOG.md` entry and regenerate the UI fixtures, which
  record the plugin version. A test checks they agree.

When Hermes changes an API the plugin uses, update `EXPECTED_*` in `hermes_compat.py` together with
the code that depends on it. The contract tests and `scripts/check_hermes_compat.py` read the same
tables.
