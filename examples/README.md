# browser-cli examples

These examples are meant for Codex and other agents that should prefer
`browser-cli` workflows over ad hoc Playwright scripts.

Generate a starter case file:

```bash
browser-cli case schema
browser-cli case scaffold --template page-inspection --url https://example.com --output case.yaml
browser-cli case scaffold --template agent-primitives --output agent-primitives-case.yaml
browser-cli case scaffold --template form-fill --output form-case.yaml
browser-cli case scaffold --template content-extraction --output content-extraction-case.yaml
browser-cli case scaffold --template browser-state --output browser-state-case.yaml
browser-cli case scaffold --template interactive-targeting --output interactive-case.yaml
browser-cli case scaffold --template page-diagnostics --output diagnostics-case.yaml
```

Case files can use agent primitives and semantic actions such as `observe`, `act`,
`extract`, `text-snapshot`, `link-snapshot`, `table-snapshot`, `list-snapshot`,
`fill`, `fill-label`, `click-role`,
`wait-text`, `get-value-role`, `interactive-snapshot`,
`accessibility-snapshot`, `console-snapshot`, and `network-snapshot`.

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

Run an interactive targeting case:

```bash
browser-cli case run --file examples/cases/interactive-targeting.yaml --close-created-session
```

Run a content extraction case:

```bash
browser-cli case run --file examples/cases/content-extraction.yaml --close-created-session
```

Run a browser state case:

```bash
browser-cli case run --file examples/cases/browser-state.yaml --close-created-session
```

Run a page diagnostics case:

```bash
browser-cli case run --file examples/cases/page-diagnostics.yaml --close-created-session
```

Case runs create browser artifacts under `/tmp/lexmount-runs` unless
`--artifacts-dir` is provided.
