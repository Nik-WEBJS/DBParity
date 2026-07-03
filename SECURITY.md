# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead:

- use GitHub's private vulnerability reporting:
  **Security → Report a vulnerability** on
  [the repository](https://github.com/Nik-WEBJS/DBParity/security/advisories/new), or
- email **nikita.fokin0123@gmail.com** (subject prefix `[dbparity security]`).

Include the DBParity version, a minimal reproduction (config + schema/DDL if
relevant), and the impact as you understand it.

## Scope

DBParity is a read-only comparison tool that connects to databases with
credentials you provide, but it still has a real attack surface. Reports are
especially welcome for:

- **SQL injection via identifiers** — table/column names from the config or
  from database catalogs are interpolated into generated queries; a crafted
  identifier must not be able to escape quoting or execute arbitrary SQL.
- **Path traversal / unsafe file writes** — report, checkpoint and state file
  paths taken from the config or CLI.
- **Data leakage when `mask_values: true`** — with masking enabled, real
  column values must never appear in the HTML/JSON report, samples, logs or
  checkpoint files (PK values are documented as unmasked). The same goes for
  credential masking (`password`/`secret`/`token`) in the report's
  `config_summary`.

Out of scope: issues that require the attacker to already control the config
file *and* the machine running DBParity, and denial-of-service by pointing
the tool at a huge or hostile database.

## Response

This is a solo-maintained open-source project, so timelines are
**best-effort**: expect an acknowledgement within about 7 days and a fix or
mitigation plan depending on severity. Please allow a reasonable disclosure
window before publishing details.

Only the latest released version is supported with security fixes.
