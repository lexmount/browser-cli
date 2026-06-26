# browser-cli examples

These examples are meant for Codex and other agents that should prefer
`browser-cli` workflows over ad hoc Playwright scripts.

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
