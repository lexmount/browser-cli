---
name: browser-cli
description: Operate Lexmount remote browser sessions through the browser-cli command line tool. Use when Codex or another agent needs to create, list, inspect, keep alive, or close Lexmount browser sessions; manage persistent browser contexts; open pages, wait for selectors, URLs, text, or form values, click, type, focus, blur, clear, submit forms, navigate history, read or mutate localStorage/sessionStorage, screenshot, evaluate JavaScript, inspect interactive elements, or snapshot page title, URL, HTML, and body text through the CLI; or verify Lexmount browser credentials without writing custom Playwright code.
---

# browser-cli

Use `browser-cli` as the primary interface for Lexmount browser automation.
Prefer CLI commands and JSON output over importing Python internals or writing
ad hoc Playwright scripts.

## Setup

Check that the CLI is available:

```bash
browser-cli --help
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
browser-cli action reload --session-id <session_id>
browser-cli action go-back --session-id <session_id>
browser-cli action go-forward --session-id <session_id>
browser-cli action wait-url --session-id <session_id> --url /dashboard
browser-cli action get-text --session-id <session_id> --selector "main"
browser-cli action exists --session-id <session_id> --selector "button"
browser-cli action count --session-id <session_id> --selector ".item"
browser-cli action query --session-id <session_id> --selector ".item" --max-nodes 20
browser-cli action get-attribute --session-id <session_id> --selector "a" --name href
browser-cli action wait-text --session-id <session_id> --text "Ready" --selector "main"
browser-cli action focus --session-id <session_id> --selector "input[name=q]"
browser-cli action get-value --session-id <session_id> --selector "input[name=q]"
browser-cli action wait-value --session-id <session_id> --selector "input[name=q]" --value "query"
browser-cli action blur --session-id <session_id> --selector "input[name=q]"
browser-cli action storage-get --session-id <session_id> --area local --key featureFlag
browser-cli action storage-set --session-id <session_id> --area local --key seenIntro --value true
browser-cli action storage-remove --session-id <session_id> --area session --key draft
browser-cli action storage-clear --session-id <session_id> --area session --prefix temp:
browser-cli action clear --session-id <session_id> --selector "input[name=q]"
browser-cli action submit --session-id <session_id> --selector "form"
browser-cli action scroll --session-id <session_id> --y 600
browser-cli action select-option --session-id <session_id> --selector "select" --value pro
browser-cli action check --session-id <session_id> --selector "input[type=checkbox]"
browser-cli action uncheck --session-id <session_id> --selector "input[type=checkbox]"
browser-cli action hover --session-id <session_id> --selector ".menu"
browser-cli action press --session-id <session_id> --selector "input[name=q]" --key Enter
browser-cli action click-text --session-id <session_id> --text "Submit"
browser-cli action click-role --session-id <session_id> --role button --name "Submit"
browser-cli action fill-label --session-id <session_id> --label "Email" --text "me@example.com"
browser-cli action accessibility-snapshot --session-id <session_id> --max-nodes 100
browser-cli action interactive-snapshot --session-id <session_id>
```

Prefer these built-in actions over writing custom JavaScript. `reload`,
`go-back`, `go-forward`, `wait-url`, `get-text`, `exists`, `count`, `query`,
`get-attribute`, `wait-text`, `focus`, `get-value`, `wait-value`, `blur`,
`storage-get`, `storage-set`, `storage-remove`, `storage-clear`, `clear`,
`submit`, `scroll`, `select-option`, `check`, `uncheck`, `hover`, and `press`
plus `click-text`, `click-role`, `fill-label`, `accessibility-snapshot`, and
`interactive-snapshot` are DOM/eval backed, so always parse their structured
`result` fields such as `found`, `exists`, `count`, `checked`, `selected`,
`clicked`, `filled`, `focused`, `value`, `readable`, `blurred`, `set`,
`removed`, `cleared`, `items`, `cleared_count`, `submitted`, `hovered`,
`pressed`, and `navigation_requested` before assuming the page changed.

For page work, choose actions in this order:

1. Inspect with `snapshot`, then `interactive-snapshot` when selectors or roles
   are unclear.
2. Prefer semantic actions: `click-role` for known roles/names, `click-text` for
   visible text, and `fill-label` for labeled form fields.
3. Use selector actions when a stable selector is known: `exists`, `count`,
   `query`, `get-attribute`, `wait-text`, `get-text`, `wait-selector`, `click`,
   `type`, `focus`, `get-value`, `wait-value`, `blur`, `clear`, `submit`,
   `select-option`, `check`, and `uncheck`.
4. Use `reload`, `go-back`, `go-forward`, and `wait-url` for navigation flows.
5. Use `storage-get`, `storage-set`, `storage-remove`, and `storage-clear` for
   localStorage/sessionStorage state instead of writing storage JavaScript.
6. Use `scroll`, `hover`, or `press` for viewport, menu, and keyboard flows.
7. Use `eval` only for page-local work not covered by a first-class action, and
   keep the expression small.
8. If `result.found`, `result.exists`, `result.clicked`, or `result.filled` is
   false, inspect again before trying a different action. For form state, parse
   `result.value` and `result.readable` before deciding whether to type again.

Common task recipes:

1. Fill and submit a form: run `interactive-snapshot`, use `fill-label` for
   labeled fields, `clear` before replacement text when needed, use
   `get-value` or `wait-value` to confirm form state, use `blur` for
   focus-driven validation, use `select-option` or `check` for controls, then
   use `submit`, `click-role --role button --name <text>` or `click-text`.
2. Click a visible control: prefer `click-role`, then `click-text`, then
   selector `click` after `exists` confirms a stable selector.
3. Navigate page history: use `reload`, `go-back`, or `go-forward`, then confirm
   with `wait-url`, `wait-text`, or `snapshot`.
4. Open menus or keyboard flows: use `focus`, `hover` for menus, `press` for
   shortcuts or Enter/Escape, and `blur` for focus-driven validation, then
   inspect again with `interactive-snapshot`.
5. Read page results: use `get-text` for a known selector; use `snapshot` when
   the page structure or selector is unknown; use `wait-text` before reading
   dynamic results.
6. Adjust browser state: use `storage-get` for local/session storage,
   `storage-set` for feature flags or onboarding state, and `storage-remove` or
   `storage-clear --prefix <prefix>` for targeted cleanup.
7. Debug selectors: use `count`, `query`, and `get-attribute` before `eval`.
8. Capture final evidence: use `screenshot` after the action sequence and close
   the session unless the user asks to keep it open.

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
