# Security policy

## Reporting a vulnerability

Please report vulnerabilities **privately** through GitHub's
[private vulnerability reporting](https://github.com/madpin/hermes-gateway-pricing/security/advisories/new)
("Security" tab -> "Report a vulnerability"). Do not open a public issue for security problems.

Include the plugin version (`hermes gateway-pricing status`), your Hermes version, and steps to
reproduce. You can expect an acknowledgement within a week.

## Scope

Of particular interest:

- URL-matching bypasses: a rule applying to a host it should not;
- anything that makes the plugin open a network connection, read a secret, or write outside
  `state.db` (backfill) and `$HERMES_HOME/gateway-pricing/backups/`;
- backfill writing rows it should not, or writing without `confirm`.

Only the latest release is supported.
