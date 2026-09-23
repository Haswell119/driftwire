# DriftWire

[![CI](https://github.com/Haswell119/driftwire/actions/workflows/ci.yml/badge.svg)](https://github.com/Haswell119/driftwire/actions/workflows/ci.yml)

Detect API contract drift before your users do.

DriftWire answers the two questions that break API integrations:

1. **Does my live API still match my OpenAPI spec?** (`driftwire check`)
2. **Did I introduce a breaking change between two spec versions?** (`driftwire diff`)

Most API tooling does one or the other. The most popular free tool (oasdiff) can only
diff two spec files — it explicitly *cannot* tell you whether your live API matches the
spec. DriftWire does both, in one zero-dependency CLI.

## Install

```bash
# Recommended (single-file wheel, no build step):
pip install "https://github.com/Haswell119/driftwire/releases/download/v0.2.0/driftwire-0.2.0-py3-none-any.whl"

# From source:
pip install git+https://github.com/Haswell119/driftwire.git

# macOS (Homebrew):
brew tap haswell119/driftwire
brew install driftwire
```

Requires Python 3.9+. Only dependency: PyYAML (for YAML specs). The Pro license
verification needs `cryptography` — `pip install driftwire[pro]`.

## Usage

### `check` — is your live API drifting from the spec?

```bash
driftwire check openapi.yaml --url https://api.example.com
```

Hits every `GET` endpoint, validates each response body against the spec's response
schema, and reports:

- **type mismatches** — spec says `string`, reality returns an integer
- **undocumented fields** — the API returns fields the spec never declared
- **missing required fields** — the spec promises a field the API stopped sending
- **enum violations** — a value that isn't in the declared enum

```text
probed 2 endpoint(s), skipped 0, errors 0
✗ 1 drift finding(s):
  - [type_mismatch] GET /todos/1 -> /userId: [HTTP 200] expected type string, got integer
```

Options: probe other methods with `--method POST --method PUT`, add auth headers with
`--header "Authorization: Bearer ***"`, fill path parameters with `--path-param id=42`.
Exit code 1 when drift is found (CI-friendly), 0 when clean.

### `diff` — breaking changes between two spec versions

```bash
driftwire diff old.yaml new.yaml
```

```text
✗ 4 drift finding(s):
  - [removed_endpoint] /legacy: endpoint removed
  - [added_required] GET /users/response 200/email: field 'email' became required
  - [removed_field] GET /users/response 200/name: field 'name' removed
  - [removed_enum] GET /users/response 200/status: enum values removed: ['inactive']
```

Detects removed endpoints/methods/parameters, newly-required fields and parameters,
removed fields, type changes, and removed enum values — the breaking changes that
silently break API consumers.

### JSON output (for CI)

Both commands accept `--json` for machine-readable results.

## Pro — HTML reports & waiver config

The free core (`check` + `diff`) is MIT and stays free. Two features are licensed:

- **HTML reports** — `--format html` renders a self-contained, severity-colored report
  you can attach to a PR or share with a reviewer.
- **`.driftwire.yml` waivers** — `--config .driftwire.yml` records reviewed-and-justified
  exceptions, so CI stays green on known drift while still blocking *new* drift (the
  workflow oasdiff sells for $100/mo):

```yaml
waivers:
  - type: type_mismatch
    path: "GET /todos/1 -> /userId"
    reason: "legacy field — JIRA-123, migrate in v3"
```

```bash
driftwire diff old.yaml new.yaml --format html --config .driftwire.yml --license "$DRIFTWIRE_LICENSE"
```

A Pro license is a one-time purchase ([buy DriftWire Pro](https://driftwire.onrender.com/))
and is verified **offline** — the CLI checks the signature locally, no phone-home. Set it
once with `export DRIFTWIRE_LICENSE="dw1…"` or pass `--license` per run.

## GitHub Action

Use DriftWire as a CI gate (breaking-change check on every PR):

```yaml
name: drift-check
on: [pull_request]
jobs:
  drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Haswell119/driftwire@main
        with:
          command: diff
          old: ${{ github.event.pull_request.base.sha }}:openapi.yaml
          new: openapi.yaml
```

Or spec-vs-reality against a deployed API:

```yaml
      - uses: Haswell119/driftwire@main
        with:
          command: check
          spec: openapi.yaml
          url: https://staging.example.com
          license: ${{ secrets.DRIFTWIRE_LICENSE }}   # for --config / HTML
```

The action exits non-zero when drift is found, so it fails the build like any other check.

## License

MIT for the free core. Pro features (HTML reports, `.driftwire.yml` waivers) require a
paid license. Built by Meridian Digital.
