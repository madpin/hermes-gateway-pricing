# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-11-13

### Added
- Desktop settings page (`desktop/plugin.js`, opt-in) under Settings → Plugins → Gateway Pricing:
  - **Overview**: status banner (active / inactive and why, warnings, valid rules, versions,
    profile), the master switch, and a "Not priced yet" list from detection with one-click
    "Add rule" pre-filled from the suggestion.
  - **Rules**: ordered list (first match wins) with enable switch, override / invalid badges,
    edit, duplicate, delete, move up / down; an editor for base URL, model globs, mode (bill like a
    vendor, explicit prices, free), multiplier, override and id, with inline validation that mirrors
    the backend and a live, debounced preview of the draft.
  - **Aliases**: key / value editor with duplicate detection.
  - **Backfill**: dry-run preview with a per-endpoint table, then apply behind a confirmation
    (state.db is backed up first). Disabled while there are unsaved changes.
  - Edits are a draft with a Save / Discard bar. Saving goes through Hermes' own
    `plugins.manage settings` writer; changes made elsewhere reload the page, or offer
    "Reload / Keep mine" when you have unsaved edits. Unknown rule keys are preserved.
- `GET /status` now also returns `vendors`: the vendors `price_as` accepts (additive).
- UI test suite in `tests/ui/` (vitest + jsdom + Testing Library against an SDK stub and
  fixtures generated from the real backend by `tests/ui/gen_fixtures.py`), and a `ui` CI job.

## [0.1.0] - 2026-11-13

### Added
- Per-endpoint pricing rules in the plugin settings (`enabled`, `rules`, `aliases`): `price_as`
  (`auto`, a vendor, or `vendor/model`), explicit `prices`, `free`, `multiplier`, `override`,
  model globs, first-match-wins evaluation.
- URL matching on parsed URLs (exact host or `*.` wildcard, port, path prefix on segment
  boundaries, IPv6), resistant to look-alike hosts, userinfo and path tricks.
- Vendor inference and Hermes-compatible model-id normalisation (Anthropic dotted versions,
  Bedrock region scopes and trailers, `vendor/model` ids).
- Runtime wrap of `agent.usage_pricing.resolve_billing_route` and `get_pricing_entry`
  (`hermes_compat.py`): idempotent, feature-detected, fail-safe, hot-reloading settings per profile.
- CLI: `hermes gateway-pricing status | detect | preview | backfill` with `--json`.
- HTTP backend: `GET /status`, `GET /detected`, `POST /preview`, `POST /backfill`.
- Backfill of unknown costs in `state.db`: dry run by default, schema check, online backup
  (5 kept), single `BEGIN IMMEDIATE` transaction, live-session skip, idempotent.
- `scripts/check_hermes_compat.py` and a CI workflow testing against Hermes `main` and the latest
  release.

[Unreleased]: https://github.com/madpin/hermes-gateway-pricing/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/madpin/hermes-gateway-pricing/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/madpin/hermes-gateway-pricing/releases/tag/v0.1.0
