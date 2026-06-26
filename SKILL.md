---
name: browser-cli
description: Operate Lexmount remote browser sessions through the browser-cli command line tool. Use when Codex or another agent needs to bootstrap Lexmount browser credentials, create, list, inspect, keep alive, or close Lexmount browser sessions; manage persistent browser contexts; open pages, wait for selectors, click, type, screenshot, evaluate JavaScript, or snapshot page title, URL, HTML, and body text through the CLI; or verify Lexmount browser credentials without writing custom Playwright code.
---

# browser-cli

Use `browser-cli` as the primary interface for Lexmount browser automation.
Prefer CLI commands and JSON output over importing Python internals or writing
ad hoc Playwright scripts.

## Setup

Check that the CLI is available:

```bash
browser-cli --help
browser-cli auth bootstrap
browser-cli auth status
```

If it is not installed, install it with:

```bash
uv tool install git+https://github.com/lexmount/browser-cli.git
```

Require credentials in the local shell:

```bash
export LEXMOUNT_API_KEY="<api-key>"
export LEXMOUNT_PROJECT_ID="<project-id>"
```

Do not ask the user to paste secrets into chat. Direct the user to
`https://browser.lexmount.cn` for China region credentials. The China region
defaults to `https://api.lexmount.cn`; set `LEXMOUNT_BASE_URL` only when a
non-default API endpoint is needed.

If credentials are missing, run:

```bash
browser-cli auth login
browser-cli auth login --open
browser-cli auth device-code
```

For credential setup, use this decision flow:

1. Run `browser-cli auth bootstrap` and parse JSON.
2. If `decision.action` is `verify_access`, run `decision.next_command` before
   browser work; this is usually `browser-cli doctor --json`.
3. If `decision.action` is `login` or `missing` includes `LEXMOUNT_API_KEY` or
   `LEXMOUNT_PROJECT_ID`, run `browser-cli auth login`. Use
   `browser-cli auth login --open` only when it is appropriate to open the
   user's local browser; otherwise show the returned `authorization_url`.
4. Use the returned `workflow`, `connect_from_codex`, and `safety_rules` to
   decide whether to login, export env lines, run doctor, or start browser work.
5. Run `browser-cli auth status` when you only need the local env state.
6. Use `browser-cli auth device-code` only to inspect or integrate the future
   Connect from Codex device-code/OAuth contract. If it returns
   `available: false`, fall back to `browser-cli auth login`.
7. Use `browser-cli auth connect-spec` when implementing or checking
   browser.lexmount.cn. Read `backend_endpoints`, `frontend_states`,
   `doctor_verification_contract`, `acceptance_tests`, and
   `credential_lifecycle` from JSON.
8. Direct the user to set credentials in their local shell, not in chat.
9. Use `browser-cli auth export-env` for masked shell snippets and
   `browser-cli auth export-env --reveal-secrets` only in a trusted local shell.
10. Treat `usable: false`, `masked: true`, or `contains_secrets: true` as a signal
   not to paste output into chat, logs, docs, tests, or commits.

Use this to generate local shell configuration snippets when credentials are
already available in the user's trusted shell:

```bash
browser-cli auth export-env
```

Only use `browser-cli auth export-env --reveal-secrets` in a trusted local
shell, and never paste revealed output into chat, logs, docs, or commits.
Use `auth login --open` only when opening the local browser is appropriate for
the user; otherwise return the `authorization_url` from JSON and let the user
open it.

## Workflow

For a one-off task:

```bash
browser-cli session create
browser-cli action open-url --session-id <session_id> --url <url>
browser-cli action snapshot --session-id <session_id>
browser-cli session close --session-id <session_id>
```

Use persistent contexts only when cookies, login state, or storage should
survive across sessions:

```bash
browser-cli context create
browser-cli session create --context-id <context_id> --context-mode read_write
```

Always close sessions created for temporary automation unless the user asks to
keep them open.

## Commands

Session lifecycle:

```bash
browser-cli auth bootstrap
browser-cli auth status
browser-cli auth device-code
browser-cli auth connect-spec
browser-cli session create
browser-cli session list
browser-cli session get --session-id <session_id>
browser-cli session close --session-id <session_id>
browser-cli session keepalive --session-id <session_id>
```

Context lifecycle:

```bash
browser-cli context create
browser-cli context list
browser-cli context get --context-id <context_id>
browser-cli context delete --context-id <context_id>
```

Browser actions:

```bash
browser-cli action open-url --session-id <session_id> --url https://example.com
browser-cli action wait-selector --session-id <session_id> --selector "main"
browser-cli action click --session-id <session_id> --selector "button"
browser-cli action type --session-id <session_id> --selector "input[name=q]" --text "query"
browser-cli action screenshot --session-id <session_id> --output /tmp/page.png
browser-cli action eval --session-id <session_id> --script "() => document.title"
browser-cli action snapshot --session-id <session_id> --max-chars 8000
```

Each action must use exactly one target:

```bash
--session-id <session_id>
--connect-url <cdp_websocket_url>
--direct-url
```

Prefer `--session-id`. Use `--direct-url` only when the user explicitly wants
the shared direct websocket path.

## Output

Parse command output as JSON. Check `ok` first, then inspect `command`,
`error`, and command-specific fields. Do not log revealed API keys. By default,
browser direct URLs are masked; use reveal flags only for local debugging.
