# gateway-pricing

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that gives you cost estimates
for sessions that run through **custom providers, local shims and LLM gateways**.

Hermes can only price first-party hosts, OpenRouter and Nous. Everything else, including a `custom`
provider, a loopback URL (a local shim, LiteLLM, Ollama in front of a cloud model) or a company LLM
gateway, is recorded as `$0.00` with `cost_status = unknown`. That `$0.00` shows up in
`hermes insights`, in the desktop cost chips and in `state.db`, and Hermes has no setting for
per-endpoint pricing.

With this plugin you declare rules such as:

* "requests to `127.0.0.1:9001` are billed like Anthropic",
* "this gateway charges OpenAI list price plus 10 %",
* "these models cost $3 / $15 per 1M tokens",
* "this endpoint is local inference and costs nothing".

Hermes' own cost pipeline then uses those rules, live, and you can recompute old sessions.

* Version 0.2.0, MIT licensed. Tested on Hermes **0.23.1**.
* No network calls, no secrets read, no new dependencies.

## 60-second quickstart

```bash
# 1. Install: the repository root is the plugin. Any directory name works.
git clone https://github.com/madpin/hermes-gateway-pricing ~/.hermes/plugins/gateway-pricing
hermes plugins enable gateway-pricing      # or add it to plugins.enabled in config.yaml

# 2. See what Hermes could not price
hermes gateway-pricing detect
```

```yaml
# 3. Add rules to ~/.hermes/config.yaml (or use Settings -> Plugins -> Gateway Pricing, see "Settings UI")
plugins:
  entries:
    gateway-pricing:
      settings:
        rules:
          - id: claude-shim
            match: {base_url: "127.0.0.1:9001"}
            price_as: anthropic
          - id: llama
            match: {base_url: "127.0.0.1:9003"}
            free: true
```

```bash
# 4. Check, then backfill old sessions (a dry run unless you pass --apply)
hermes gateway-pricing preview claude-opus-5-5 http://127.0.0.1:9001
hermes gateway-pricing backfill
hermes gateway-pricing backfill --apply
```

New turns are priced within about 2 seconds of saving a rule. No restart is needed.

## How it works

```
 agent turn
     │ usage (tokens), model, provider, base_url
     ▼
 agent.usage_pricing.estimate_usage_cost()            (Hermes, unchanged)
     │
     ├─► resolve_billing_route()  ── wrapped ──┐
     │        1. Hermes' original runs first   │  billing_mode == "unknown"
     │        2. a rule matches? ──────────────┤  (or the rule has override: true)
     │             no  → Hermes' route as is   │
     │             yes → price_as: vendor route (anthropic / openai / google ...)
     │                   free:     "subscription_included" route ($0, status "included")
     │                   prices:   "user_override" route
     │
     └─► get_pricing_entry()      ── wrapped ──┐
              prices:   the rule's own rates (source "user_override")
              price_as: Hermes' own vendor rates × multiplier
              anything else: Hermes' original result
     ▼
 CostResult → session totals, session_model_usage, insights, desktop chips
```

At load, `hermes_compat.py` replaces two attributes of `agent.usage_pricing`. Hermes looks both
names up as module globals at call time, so every consumer sees the change, including code that ran
`from agent.usage_pricing import estimate_usage_cost`. `estimate_usage_cost` itself is never replaced.

* **Untouched by default.** Routes Hermes already prices stay byte-for-byte the same unless a rule
  says `override: true`. The tests check this for Anthropic direct and OpenRouter.
* **Idempotent and fail-safe.** The wrap is installed once. If anything inside the plugin raises,
  Hermes' original result is returned. If Hermes' API changed (see `scripts/check_hermes_compat.py`),
  nothing is patched, one WARNING is logged, and `status` reports `active: false` with the reason.
* **Cheap.** The plugin re-reads its settings at most every 2 seconds per profile and re-parses them
  only when they changed. Decisions are memoised, and each call does no I/O and no INFO logging.
  The benchmark test holds the added cost under 50 µs per call with 20 rules; it is typically a
  few µs.
* **Clean layout.** Rule parsing, URL matching, vendor inference and backfill logic live in modules
  with no Hermes imports. `hermes_compat.py` is the only module that touches Hermes internals.

## Configuration reference

The settings live at `plugins.entries.gateway-pricing.settings`. Each profile has its own, in that
profile's `config.yaml`.

| key | type | default | meaning |
|---|---|---|---|
| `enabled` | bool | `true` | `false` leaves every Hermes price untouched (the wrap stays installed but inert) |
| `rules` | list | `[]` | rules, evaluated top to bottom; the first match wins |
| `aliases` | dict | `{}` | `model id -> model id`, applied before vendor inference and price lookup |

### A rule

```yaml
- id: my-claude-shim            # optional label (unique); reported as matched_rule, else the 0-based index
  enabled: true                 # optional, default true
  match:
    base_url: "127.0.0.1:9001"  # host[:port][/path-prefix]; scheme ignored; "*.example.com" wildcard
    models: ["claude-*"]        # optional globs (case-insensitive), default: all models
  # exactly ONE of:
  price_as: auto                # auto | anthropic | openai | google | deepseek | xai | bedrock | fireworks |
                                #   minimax | minimax-cn | xiaomi | "vendor/model"
  prices: {input: 3.0, output: 15.0, cache_read: 0.3, cache_write: 3.75}   # USD per 1M tokens
  free: true                    # local inference: status "included", $0
  multiplier: 1.0               # optional (> 0): scales whatever price the mode produced
  override: false               # optional: also replace routes Hermes already prices
```

**Matching** works on the parsed URL, never on substrings:

* The hostname must be equal, ignoring case and a trailing dot. `*.example.com` matches any strict
  subdomain, not `example.com` itself.
* A port in the pattern must equal the URL's effective port: the explicit port, else 80/443 from
  the scheme. A pattern without a port matches any port.
* A path prefix matches on a segment boundary: `/openai` matches `/openai/v1` but not
  `/openai-beta`. `.`/`..` segments are resolved first.
* IPv6 works: `"[::1]:9001"`.
* The following never match:
  * `https://gateway.example.com.evil.com`
  * `http://evil.com/gateway.example.com`
  * `https://gateway.example.com@evil.com`
  * any URL containing a backslash, whitespace or a control character

**Models.** `match.models` globs are tested against three forms of the id Hermes reports: the
full id, its last `/` segment, and its Bedrock-unwrapped form. So `claude-*` matches
`anthropic/claude-opus-5-5` and `us.anthropic.claude-opus-5-5-v1:0`.

**`price_as: auto`** infers the vendor from the id (after aliases):

| id looks like | vendor |
|---|---|
| `claude*`, `anthropic/…`, `us.anthropic.…` | anthropic |
| `gpt-*`, `chatgpt-*`, `o1`/`o3`/`o4…` | openai |
| `gemini*` | google |
| `deepseek*` | deepseek |
| `grok*` | xai |

If the vendor cannot be inferred, the rule does not apply to that model and evaluation continues
with the next rule.

Model ids are normalised the way Hermes does:

* Anthropic dotted versions become dashes (`opus-4.7` → `opus-4-7`).
* Bedrock ids drop the region scope and the `-v1:0` / date trailers.
* `vendor/model` keeps the model part.

`price_as: openai/gpt-6-luna` forces both the vendor and the model.

**Status and source.** Vendor-aliased prices keep Hermes' own source (`official_docs_snapshot` or
`provider_models_api`) and its pricing version. `prices:` rules report source `user_override` and
pricing version `gateway-pricing:0.2.0`. `free:` reports `included`.

**Invalid rules** are skipped with one warning each, logged once per distinct problem. The other
rules keep working. Unknown keys are treated as errors so typos do not fail silently.

### Examples

```yaml
plugins:
  entries:
    gateway-pricing:
      settings:
        aliases:
          chat-latest: gpt-6.1-sol          # a gateway alias for a real model
        rules:
          # A local Claude shim (also handles Bedrock-style ids like us.anthropic.claude-...)
          - id: claude-shim
            match: {base_url: "127.0.0.1:9001"}
            price_as: anthropic

          # A local Gemini shim
          - id: gemini-shim
            match: {base_url: "127.0.0.1:9002/v1"}
            price_as: google

          # An OpenAI-compatible company gateway that adds a 10 % markup
          - id: gateway-openai
            match: {base_url: "gateway.example.com/openai"}
            price_as: openai
            multiplier: 1.1

          # The same gateway's Anthropic route, only for Claude models
          - id: gateway-anthropic
            match: {base_url: "gateway.example.com/anthropic", models: ["claude-*"]}
            price_as: anthropic

          # Free local inference (llama.cpp / llama-server)
          - id: llama
            match: {base_url: "127.0.0.1:9003"}
            free: true

          # Explicit prices for a model Hermes has no table for
          - id: house-model
            match: {base_url: "*.inference.example.com", models: ["house-*"]}
            prices: {input: 0.5, output: 1.5, cache_read: 0.05}
```

`list` and `dict` settings are edited as JSON in the automatic settings form. The same rule as
JSON:

```json
[{"id": "claude-shim", "match": {"base_url": "127.0.0.1:9001"}, "price_as": "anthropic"}]
```

## Settings UI

The plugin folder also carries a Hermes Desktop half, `desktop/plugin.js`. It adds a
**Gateway Pricing** entry under Settings → Plugins, so you can manage rules without editing YAML.

### Turning it on

The desktop half is opt-in and ships inside the plugin folder you already installed.

1. The Python half must be active: `gateway-pricing` is in `plugins.enabled` for the profile
   (`hermes plugins enable gateway-pricing`), and the gateway was restarted afterwards so the
   plugin's API is mounted at `/api/plugins/gateway-pricing/`.
2. In Hermes Desktop, open **Settings → Plugins** (or **Capabilities → Plugins**), find
   *Gateway Pricing* and switch it on.
3. Reload desktop plugins (or restart Desktop). The entry appears in the Settings rail. Hermes'
   automatic schema form for the same settings stays available as the entry's *Agent settings*
   sub-page.

### What each page does

* **Overview** (the entry's landing page)
  * A status banner: active or inactive and why, the active profile, plugin and Hermes versions,
    how many rules are valid, and every warning the backend reports.
  * The master switch (`enabled`).
  * **Not priced yet**: endpoint/model pairs from the last 30 days that no rule prices, with
    sessions, tokens and the backend's suggestion (confidence and reason). **Add rule** opens the
    rule editor pre-filled from the suggestion: bill like the suggested vendor, or free for local
    inference. Without a suggestion it is pre-filled with explicit prices for you to fill in.
* **Rules**: the ordered list. The first rule that matches wins. Each row has an enable switch, the
  rule id (or "Rule N"), what it matches, how it prices ("bills like anthropic", "$3 / $15 per 1M",
  "free", "x1.1"), an *override* badge, and an *invalid* badge with the backend's warning when
  Hermes would skip the rule. You can edit, duplicate, delete (with a confirmation) and move rules
  up or down.
  * The **editor** has fields for the base URL, model globs (comma separated), the mode (*Bill like
    a vendor*, *Explicit prices*, *Free*), the vendor (`auto` or a vendor Hermes knows, optionally
    a specific vendor model), prices in USD per 1M tokens (input, output, cache read, cache write),
    the multiplier, override, and an optional id. Fields are checked as you type, with the same
    rules the backend applies.
  * The **live preview** prices 1M input + 1M output tokens for a model and base URL you type. They
    default to the rule's host and first literal model. It uses your unsaved draft and shows the
    rule that matched (or that an earlier rule wins), the cost status and source, the amount, the
    per-1M rates and the reason.
  * Fields the editor does not know are kept as they are when you edit a rule.
* **Aliases**: rows of *model id → price as*, with add and remove. Duplicate ids and half-filled
  rows are flagged, and Save is blocked until they are fixed.
* **Backfill**: choose a period, then **Preview changes** (a dry run) to see how many usage rows and
  sessions would change, the cost added, and a per-endpoint table. **Apply** asks for confirmation,
  backs up `state.db` first and shows the backup path. Backfill always uses the *saved* rules, so
  both buttons are disabled while you have unsaved changes.

### Saving

Edits are kept in a draft. While there are unsaved changes, a **Save / Discard** bar stays at the
bottom of every page. Save writes `enabled`, `rules` and `aliases` through Hermes' own settings
writer (`plugins.manage settings`), for the profile the desktop's REST calls target (the active
profile shown in the banner). If Hermes refuses, for example on a managed install, the page shows
the reason and keeps your draft.

If the settings change somewhere else (another window, the CLI, `config.yaml`), the page reloads.
If you have unsaved edits it asks instead: **Reload** takes the other change, **Keep mine** keeps
your draft and overwrites the other change when you save.

When the backend cannot be reached, each page explains why and what to do next:

* the API is not mounted: enable the plugin and restart the gateway;
* the pricing wrap is not active;
* the request timed out;
* no `state.db` exists yet.

### Without the desktop half

You can still edit the same settings in Hermes' automatic form (Settings → Plugins →
gateway-pricing, where `rules` and `aliases` are JSON editors) or in the profile's `config.yaml`.
Edits take effect within about 2 seconds, without a restart.

### Status

The settings page is new in 0.2.0.

* It is covered by jsdom tests (`tests/ui/`) that run against a stub of the desktop SDK contract.
  The stub's component props are checked against the real Hermes sources, and the tests use
  responses captured from the real backend.
* It has not yet been exercised in a packaged Hermes Desktop build.

If something looks wrong, please open an issue with the
[bug report template](.github/ISSUE_TEMPLATE/bug_report.md). Include the Hermes Desktop version and
`hermes gateway-pricing status --json`.

## CLI

Every command accepts `--json`.

```bash
hermes gateway-pricing status                       # active? rules valid? warnings
hermes gateway-pricing detect [--days 30]           # endpoints/models Hermes recorded without a price
hermes gateway-pricing preview MODEL BASE_URL [--provider custom]
hermes gateway-pricing backfill [--days N]          # dry run: what would change
hermes gateway-pricing backfill [--days N] --apply  # write it (backup first)
```

* `preview` prices 1M input + 1M output tokens with the saved rules. 1M prompt tokens is above
  some vendors' long-context tier, so Hermes' whole-request "above" rates apply (for example
  gpt-6.x above 272K).
* `detect` prints a suggestion per unpriced endpoint/model. `high` confidence means Hermes has a
  list price for the inferred vendor model; `low` means it does not, or that the endpoint looks
  like local inference.

### HTTP backend

Hermes mounts the backend at `/api/plugins/gateway-pricing/`. Every route honours `?profile=` and
returns JSON only.

| route | body / query | returns |
|---|---|---|
| `GET /status` | | `{active, reason, plugin_version, hermes_version, rules_total, rules_valid, warnings, wrapped, vendors}` |
| `GET /detected` | `?days=30` | `[{base_url, host, model, sessions, calls, input_tokens, output_tokens, cache_read_tokens, last_seen, matched_rule, suggestion}]` |
| `POST /preview` | `{model, base_url, rules?, aliases?, usage?}` | `{matched_rule, mode, status, source, amount_usd, per_million, route, pricing_version, reason, warnings}` |
| `POST /backfill` | `{dry_run: true, days?, rules?, confirm?}` | `{dry_run, rows_considered, rows_updated, sessions_updated, delta_usd, by_endpoint, backup_path, ...}` |

* `preview` and `backfill` accept **draft** rules and never save them.
* Rules are saved through Hermes' own settings writer, so the backend has no write route for
  settings. From the desktop SDK the call is
  `host.request('plugins.manage', {action: 'settings', key: 'gateway-pricing', values: {rules: [...]}})`.
  Hermes validates the value types against `config_schema` and broadcasts
  `plugin.gateway-pricing.settings.changed`.
* Errors return `{"detail": ...}`:

  | status | meaning |
  |---|---|
  | 400 | bad input |
  | 404 | no `state.db` |
  | 409 | refused: unexpected schema, missing `confirm`, or a busy database |
  | 503 | the pricing wrap is not active in this process |

## Backfill

Backfill recomputes costs Hermes stored as unknown. It edits your session database, so it is
written defensively:

* **Dry run by default.** It reads through a read-only connection (`mode=ro`), and the file is not
  modified. Writing needs `--apply` (API: `dry_run: false` **and** `confirm: true`).
* **Narrow scope.**
  * Only `session_model_usage` rows with `cost_status` NULL, `''` or `unknown` and some tokens are
    repriced.
  * `estimated` / `included` rows are repriced only when their endpoint matches a rule with
    `override: true`.
  * `actual` rows (provider invoices) are **never** touched, even with `override`, and
    `actual_cost_usd` is never written.
  * Rows no rule matches are left alone and are not priced at all: Hermes would query the
    endpoint, and the plugin makes no network calls.
* **Session totals.**
  * A repriced session's total becomes the sum of its usage rows. A session without usage rows is
    priced from its own token columns.
  * Changed sessions get `pricing_version = "gateway-pricing:<plugin version>"` (for example
    `gateway-pricing:0.2.0`) and Hermes' `cost_status` /
    `cost_source` values, so they are easy to recognise.
  * A session Hermes already priced keeps its total even if one of its auxiliary rows (title
    generation, say) was repriced, unless an `override` rule matches. It is counted in
    `sessions_skipped_priced`.
* **Live sessions are skipped.** A session with no `ended_at` and activity in the last 10 minutes
  is left alone, because Hermes may overwrite its totals.
* **Backup first.**
  1. Before the first write, SQLite's online-backup API copies the database to
     `$HERMES_HOME/gateway-pricing/backups/state-<UTC timestamp>.db`. The 5 newest copies are kept.
  2. The plugin then takes `BEGIN IMMEDIATE` with a busy timeout. If another process wrote between
     the backup and the lock, it rolls back and starts over.
  3. It re-plans inside the transaction and applies everything in that one transaction. Any error
     rolls everything back.
* **Schema check.** Before reading or writing, the plugin checks `sessions` and
  `session_model_usage` with `PRAGMA table_info`. If the columns are not the ones it expects, it
  refuses and writes nothing.
* **Idempotent.** A row is written only when its cost, status or source changes, so a second run
  changes 0 rows.

To restore, stop Hermes and copy a backup over `state.db`.

`session_model_usage` has no `pricing_version` column, so repriced usage rows are recognisable
only by their `cost_status` / `cost_source`. The `pricing_version` marker is on the session rows.

## Limits

* **Estimates at list price, not invoices.** Your contract, discounts, batch pricing or free tiers
  are not known. Use `multiplier` or `prices:` to approximate them.
* **Gateways that rewrite models.** The plugin prices the model id Hermes reports. If the gateway
  silently routes `chat-latest` to something else, add an alias. If it routes by load, no rule can
  know.
* **Models Hermes has no price for.** `price_as` uses Hermes' own tables: the bundled snapshot,
  plus a cached models.dev catalogue that is never fetched by this plugin. A model missing from
  both stays `unknown`; `preview` and `detect` say so. Use `prices:` instead.
* **Missing cache rates.** Tokens in a bucket without a rate, for example `cache_read` tokens with
  no `prices.cache_read`, make the estimate `unknown`. This is Hermes' rule. Set the cache rates
  if your traffic uses caching.
* **Multipliers and fast tiers.** `multiplier` does not apply to Anthropic fast-mode or OpenAI
  Ultrafast responses, which Hermes prices from separate tables.
* **Subscription plans are out of scope.** Amp, Devin and other `process://` routes are flat-fee
  plans, not per-token. You can mark them `free: true` (status `included`), but no rule can split a
  subscription into per-session costs.
* **Process-wide wrap.** In a gateway that serves several profiles, the wrap is process-wide and
  the rules are read from the active profile's settings. A profile with no
  `plugins.entries.gateway-pricing.settings` is unaffected. A profile that disabled the plugin but
  still has rules saved will still have them applied while the process runs the plugin for another
  profile. To stop that, set `enabled: false` or delete its rules.

## Why this is not in the plugin catalog

Hermes' admission lint (`hermes plugins validate`, `hermes_cli.plugin_validate`) refuses plugins
that rebind core attributes at runtime, and this plugin has to do exactly that. Hermes offers no
public hook to price an endpoint, and `ProviderProfile.get_usage_cost(model, usage)` never sees the
base URL. The validator's finding on this repository, verbatim:

```
FAIL no core override - rebinds Hermes core at runtime (use a public hook, middleware or provider profile): up.resolve_billing_route (hermes_compat.py:449); up.get_pricing_entry (hermes_compat.py:450); up.get_pricing_entry (hermes_compat.py:465); up.resolve_billing_route (hermes_compat.py:466)
WARN private core imports (catalog rule 9: public surfaces only; these can break on any release): hermes_compat.py:103 agent.usage_pricing; hermes_compat.py:125 hermes_cli.plugins_manifest.running_hermes_version; hermes_compat.py:133 hermes_cli. Use ctx.* / documented plugin APIs, or open an issue for the seam you need.
```

Lines 449–450 install the wrap; lines 465–466 restore the originals in `uninstall()`. Every other
admission check passes, including manifest, config schema, capability probe and security scan.
The private-import warnings are confined to `hermes_compat.py`.

The rebind is **not** hidden: no `setattr` tricks and no indirection to get past the lint. That
lint is a safety check, and you should be able to see exactly what this plugin does:

* it lives in one small module, `hermes_compat.py`;
* `scripts/check_hermes_compat.py` verifies the exact Hermes API it relies on.

If Hermes ever adds a public pricing hook, the plugin will move to it.

## Security and privacy

* **No network.** The plugin never opens a connection. For an unmatched endpoint it also avoids
  Hermes' own `/models` probe in preview, detect and backfill.
* **No secrets.** The plugin never reads API keys and passes `api_key` through untouched. Responses
  contain no secrets.
* **Reads `state.db` read-only** (`mode=ro`). The only writes are an explicit, confirmed backfill
  and its backups under `$HERMES_HOME/gateway-pricing/backups/`.
* **Settings writes** go through Hermes' own writer, never through this plugin.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Compatibility

* Tested on Hermes **0.23.1** (`requires_hermes: ">=0.23.1"`). CI also runs against Hermes `main`
  and the latest release tag.
* Python 3.11 – 3.14.
* `scripts/check_hermes_compat.py` exits non-zero, with a readable report, when an installed
  Hermes no longer offers what the plugin needs.

## Uninstall

```bash
hermes plugins disable gateway-pricing
rm -rf ~/.hermes/plugins/gateway-pricing        # wherever you cloned it
```

Prices already written by a backfill stay in `state.db`, and backups stay in
`~/.hermes/gateway-pricing/backups/`. To undo a backfill, restore a backup while Hermes is stopped.
You can also delete the `plugins.entries.gateway-pricing` block from `config.yaml`.

## Troubleshooting

Start with `hermes gateway-pricing status`. It exits 0 when active and 2 when inactive.

| status output | meaning | what to do |
|---|---|---|
| `ACTIVE`, `wrapped: resolve_billing_route, get_pricing_entry` | working | — |
| `INACTIVE`, reason `... Hermes changed an API ...` | your Hermes version moved an API the plugin needs; nothing was patched | run `python scripts/check_hermes_compat.py` with Hermes' Python and open an issue with its output |
| `INACTIVE`, reason `not installed in this process` | the plugin's `register()` did not run where you asked (e.g. not enabled for this profile) | `hermes plugins enable gateway-pricing`; restart the gateway |
| `rules: 2 valid of 3` + `warning: rules[1] ...` | a rule is invalid and skipped | fix the named key; the warning says what is wrong |
| `warning: disabled in settings (enabled: false)` | the plugin is inert by choice | set `enabled: true` |

* **A session still shows $0.**
  1. `preview MODEL BASE_URL` shows which rule matched and why.
  2. `matched rule: -` means no rule matches that URL/model.
  3. `status: unknown` with a match means Hermes has no price for the vendor model. Add an alias
     or `prices:`.
  4. Old sessions need `backfill`.
* **`backfill` says "state.db is busy".** Retry when Hermes is idle. Nothing was written.

## FAQ

**Does it change what I pay?** No. It only changes what Hermes *estimates* and displays.

**Will `hermes update` overwrite it?** No. The plugin lives in `$HERMES_HOME/plugins/`, outside the
Hermes checkout.

**What if I install it under another directory name?** That works. For a top-level user plugin,
Hermes uses the manifest name (`gateway-pricing`) as the plugin id, and the plugin uses
`ctx.plugin_id` rather than a hardcoded name.

**Can a rule change prices of Anthropic direct or OpenRouter?** Only with `override: true`.

**Why does a free route show "included" rather than "$0.00 estimated"?** That is Hermes' own
convention for routes with no per-token bill. It keeps "free" distinct from "unknown".

**Does preview save my draft rules?** No. Drafts are evaluated in memory only.

## License

MIT, © 2026 madpin. See [LICENSE](LICENSE).
