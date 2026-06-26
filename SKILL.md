---
name: browser-cli
description: Operate Lexmount remote browser sessions through the browser-cli command line tool. Use when Codex or another agent needs to create, list, inspect, keep alive, or close Lexmount browser sessions; manage persistent browser contexts; open pages, wait for selectors, click, type, screenshot, evaluate JavaScript, or snapshot page title, URL, HTML, and body text through the CLI; or verify Lexmount browser credentials without writing custom Playwright code.
---

# browser-cli

Use `browser-cli` as the primary interface for Lexmount browser automation.
Prefer CLI commands and JSON output over importing Python internals or writing
ad hoc Playwright scripts.

Use the CLI for browser work that needs remote pages, authenticated browser
state, screenshots, DOM inspection, form filling, or repeatable agent actions.
Do not use this skill for local static HTML parsing when a normal file parser is
enough.

## Setup

Check that the CLI is available:

```bash
browser-cli --help
browser-cli auth status
browser-cli doctor --json
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
```

Use this to generate local shell configuration snippets when credentials are
already available in the user's trusted shell:

```bash
browser-cli auth export-env
```

Only use `browser-cli auth export-env --reveal-secrets` in a trusted local
shell, and never paste revealed output into chat, logs, docs, or commits.

## Doctor

Run `browser-cli doctor --json` before the first browser action in a thread,
after credential changes, or when a session/context/action command fails for an
unclear reason. Parse the JSON before deciding what to do:

- `status: "pass"`: continue with browser work.
- `status: "warn"`: continue only when all failed checks have `severity:
  "warning"` and the warning does not block the requested task.
- `status: "fail"` or `ok: false`: stop before creating sessions and follow
  `next_steps`. Ask the user to fix local setup or credentials when needed.

Use `browser-cli doctor --skip-api` only for offline setup checks or when the
user explicitly asks to avoid a live API call. Do not treat a skipped API check
as proof that browser sessions will work.

## Workflow

For a one-off task, create a temporary session, inspect before acting, and close
the session when done:

```bash
browser-cli doctor --json
browser-cli session create
browser-cli action open-url --session-id <session_id> --url <url>
browser-cli action snapshot --session-id <session_id>
browser-cli action wait-selector --session-id <session_id> --selector <selector>
browser-cli session close --session-id <session_id>
```

Use persistent contexts only when cookies, login state, or storage should
survive across sessions:

```bash
browser-cli context create
browser-cli session create --context-id <context_id> --context-mode read_write
```

Use `read_write` for login/setup work that should update cookies or storage. Use
`read_only` when inspecting an existing logged-in state. Always close temporary sessions
unless the user asks to keep them open. Before deleting a context, confirm that
the task no longer needs its login state.

If a command fails, parse the JSON error first. For configuration or credential
errors, stop browser work and guide the user to configure local environment
variables. For missing selectors, take a fresh snapshot or screenshot before
choosing another selector.

## Commands

Session lifecycle:

```bash
browser-cli auth status
browser-cli doctor --json
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

Prefer built-in actions in this order:

1. Use `snapshot` or `screenshot` to understand the page.
2. Use `wait-selector`, `click`, and `type` for common DOM work.
3. Use `eval` through `browser-cli action eval` for small page-local reads or
   actions that are not yet first-class CLI commands.
4. Write custom Playwright only when the CLI cannot express the task and explain
   why the CLI was insufficient.

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
`error`, `message`, and command-specific fields. Treat nonzero exits as JSON
failures whenever output is present.

Do not log revealed API keys. Do not paste API keys, Project IDs, or full direct
connect URLs into chat, docs, commits, screenshots, or test fixtures. By
default, browser direct URLs are masked. Use reveal flags only for local
debugging in a trusted shell.

Run `browser-cli doctor --json` before browser work when setup may be stale. If
`doctor` returns `ok: false`, follow its `next_steps` before creating sessions.
