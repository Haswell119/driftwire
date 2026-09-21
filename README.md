# DriftWire

Detect API contract drift before your users do.

DriftWire answers the two questions that break API integrations:

1. **Does my live API still match my OpenAPI spec?** (`driftwire check`)
2. **Did I introduce a breaking change between two spec versions?** (`driftwire diff`)

Most API tooling does one or the other. The most popular free tool (oasdiff) can only
diff two spec files — it explicitly *cannot* tell you whether your live API matches the
spec. DriftWire does both, in one zero-dependency CLI.

## Install

```bash
pip install .            # from this repo
# or
pip install git+https://github.com/Haswell119/driftwire.git
```

Requires Python 3.9+. Only dependency: PyYAML (for YAML specs).

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
`--header "Authorization: Bearer x"`, fill path parameters with `--path-param id=42`.
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

## GitHub Actions

```yaml
name: drift-check
on: [pull_request]
jobs:
  drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install "git+https://github.com/Haswell119/driftwire.git"
      - run: driftwire diff $(git merge-base HEAD origin/main):openapi.yaml openapi.yaml --json
```

## Roadmap

Free today: `check` + `diff`, local and CI. Coming (paid, once built): HTML drift
reports and a `.driftwire.yml` waiver/exception config so teams can approve known drift
while still blocking new drift.

## License

MIT. Built by Meridian Digital.
