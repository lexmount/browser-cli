# browser-cli

Standalone CLI for operating Lexmount browser sessions.

The CLI surface is extracted from `website-skills/lex_browser_runtime/cli.py`.
Runtime behavior stays in the `lex-browser-runtime` package, so this repository
owns command parsing and packaging while avoiding a second copy of browser
session/action implementation logic.

## Install

```bash
pip install browser-cli
```

For local development:

```bash
uv sync --all-groups
```

## Commands

```bash
browser-cli session create
browser-cli session list
browser-cli session get --session-id <session_id>
browser-cli session close --session-id <session_id>
browser-cli context list
browser-cli action open-url --session-id <session_id> --url https://example.com
browser-cli action snapshot --session-id <session_id>
browser-cli case validate --file case.yaml
browser-cli case run --file case.yaml
browser-cli direct-url
```

The CLI reads Lexmount credentials from the same environment variables used by
`lex-browser-runtime`:

- `LEXMOUNT_API_KEY`
- `LEXMOUNT_PROJECT_ID`
- `LEXMOUNT_BASE_URL`
- `LEXMOUNT_REGION`
