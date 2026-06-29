# browser-cli examples

These examples are meant for Codex and other agents that should prefer
`browser-cli` workflows over ad hoc Playwright scripts.

Generate a starter case file:

```bash
browser-cli case schema
browser-cli case scaffold --template page-inspection --url https://example.com --output case.yaml
browser-cli case scaffold --template form-fill --output form-case.yaml
```

Case files can use semantic actions such as `fill`, `fill-label`, `click-role`,
`wait-text`, `get-value-role`, `interactive-snapshot`, and
`accessibility-snapshot`.

Validate all case files:

```bash
for file in examples/cases/*.yaml; do
  browser-cli case validate --file "$file"
done
```

Run a self-contained case:

```bash
browser-cli case run --file examples/cases/form-fill.yaml --close-created-session
```

Run a page inspection case:

```bash
browser-cli case run --file examples/cases/page-inspection.yaml --close-created-session
```

Case runs create browser artifacts under `/tmp/lexmount-runs` unless
`--artifacts-dir` is provided.
