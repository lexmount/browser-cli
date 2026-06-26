---
name: browser-cli
description: Operate Lexmount remote browser sessions through the browser-cli command line tool. Use when Codex or another agent needs to create, list, inspect, keep alive, or close Lexmount browser sessions; manage persistent browser contexts; open pages, wait for selectors, click, type, screenshot, evaluate JavaScript, or snapshot page title, URL, HTML, and body text through the CLI; or verify Lexmount browser credentials without writing custom Playwright code.
---

# browser-cli

Use `browser-cli` as the primary interface for Lexmount browser automation.
Prefer CLI commands and JSON output over importing Python internals or writing
ad hoc Playwright scripts.

## Setup

Check that the CLI is available:

```bash
browser-cli --help
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

## Workflow

For a one-off task:

```bash
browser-cli doctor --json
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

Run `browser-cli doctor --json` before browser work when setup may be stale. If
`decision.ready_for_browser_work` is not `true`, follow
`decision.recommended_action`, `decision.next_command`, `workflow.primary_command`,
and `next_steps` before creating sessions. Prefer `workflow.primary_command`
when it is present, and only continue to browser work when
`workflow.can_start_browser_work` is true. Use
`browser-cli doctor --smoke-session --json` only when onboarding or debugging
session lifecycle issues; it creates and closes a temporary browser session.

For smoke-session checks:

1. Prefer plain `browser-cli doctor --json` for routine readiness checks.
2. Use `browser-cli doctor --smoke-session --json` only to prove session create
   and close permissions, quota, and project access.
3. If the smoke check fails after creating a session, inspect `session_smoke` and
   follow `next_steps`; there may be a temporary session that needs cleanup.
4. Do not run smoke checks in tight loops or before every action because they
   consume browser session capacity.
5. Use `--smoke-browser-mode light` unless the user explicitly needs another
   browser mode verified.
