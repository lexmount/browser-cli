---
name: browser-cli
description: Operate Lexmount remote browser sessions through the browser-cli command line tool. Use when Codex or another agent needs to create, list, inspect, keep alive, or close Lexmount browser sessions; manage persistent browser contexts, pick reusable contexts, or detect locked contexts; guide authentication with auth status/export-env/login; verify installation, environment, and API connectivity with doctor; open pages, wait for selectors, URLs, load state, network idle, text, or form values, click, type, focus, blur, clear, submit forms, navigate history, read or mutate localStorage/sessionStorage and document.cookie-visible cookies, screenshot, evaluate JavaScript, inspect interactive elements, or snapshot page title, URL, HTML, and body text through the CLI; or verify Lexmount browser credentials without writing custom Playwright code.
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

Use local auth helpers instead of handling secrets in chat:

```bash
browser-cli auth status
browser-cli auth login
browser-cli auth export-env
```

`auth export-env` prints placeholders by default. With `--from-current`, it
still masks `LEXMOUNT_API_KEY` unless `--reveal-secrets` is explicitly used in
a trusted local terminal.

After credentials are configured, run:

```bash
browser-cli doctor
```

Parse failed `checks` if `doctor` returns `ok: false`. Use
`browser-cli doctor --skip-api` only when live API access is intentionally
unavailable.

## Workflow

If setup is uncertain, run `browser-cli auth status`, then `browser-cli doctor`
before creating a session. If credentials are missing, run
`browser-cli auth login` and guide the user to set local environment variables.

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
browser-cli context pick --metadata-json '{"purpose":"codex-login"}' --create-if-missing
browser-cli session create --context-id <context_id> --context-mode read_write
```

Use `context status --context-id <context_id>` before reuse when the context id
came from older notes. Reuse only when `reusable` is true; if `locked` is true,
pick or create a different context.

Always close sessions created for temporary automation unless the user asks to
keep them open.

## Commands

Authentication:

```bash
browser-cli auth status
browser-cli auth login
browser-cli auth export-env
browser-cli auth export-env --from-current --include-base-url
```

Diagnostics:

```bash
browser-cli doctor
browser-cli doctor --skip-api
```

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
browser-cli context status --context-id <context_id>
browser-cli context pick --metadata-json '{"purpose":"codex-login"}'
browser-cli context pick --metadata-json '{"purpose":"codex-login"}' --create-if-missing
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
browser-cli action wait-load-state --session-id <session_id> --state complete
browser-cli action wait-network-idle --session-id <session_id> --idle-ms 500
browser-cli action get-text --session-id <session_id> --selector "main"
browser-cli action exists --session-id <session_id> --selector "button"
browser-cli action count --session-id <session_id> --selector ".item"
browser-cli action wait-count --session-id <session_id> --selector ".item" --count 3 --comparison gte
browser-cli action query --session-id <session_id> --selector ".item" --max-nodes 20
browser-cli action get-attribute --session-id <session_id> --selector "a" --name href
browser-cli action wait-attribute --session-id <session_id> --selector "button" --name aria-busy --state absent
browser-cli action wait-text --session-id <session_id> --text "Ready" --selector "main"
browser-cli action focus --session-id <session_id> --selector "input[name=q]"
browser-cli action get-value --session-id <session_id> --selector "input[name=q]"
browser-cli action wait-value --session-id <session_id> --selector "input[name=q]" --value "query"
browser-cli action blur --session-id <session_id> --selector "input[name=q]"
browser-cli action storage-get --session-id <session_id> --area local --key featureFlag
browser-cli action storage-set --session-id <session_id> --area local --key seenIntro --value true
browser-cli action storage-remove --session-id <session_id> --area session --key draft
browser-cli action storage-clear --session-id <session_id> --area session --prefix temp:
browser-cli action wait-storage --session-id <session_id> --area local --key authToken
browser-cli action cookie-get --session-id <session_id> --name consent
browser-cli action cookie-set --session-id <session_id> --name consent --value yes --path /
browser-cli action cookie-delete --session-id <session_id> --name consent --path /
browser-cli action cookie-clear --session-id <session_id> --prefix tmp: --path /
browser-cli action wait-cookie --session-id <session_id> --name consent --value yes
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
`go-back`, `go-forward`, `wait-url`, `wait-load-state`, `wait-network-idle`,
`get-text`, `exists`, `count`, `query`, `get-attribute`, `wait-count`,
`wait-attribute`, `wait-text`, `focus`, `get-value`, `wait-value`, `blur`,
`storage-get`, `storage-set`, `storage-remove`, `storage-clear`,
`wait-storage`, `cookie-get`, `cookie-set`, `cookie-delete`, `cookie-clear`,
`wait-cookie`, `clear`, `submit`, `scroll`, `select-option`, `check`,
`uncheck`, `hover`, and `press` plus `click-text`, `click-role`,
`fill-label`, `accessibility-snapshot`, and
`interactive-snapshot` are DOM/eval backed, so always parse their structured
`result` fields such as `found`, `exists`, `count`, `checked`, `selected`,
`clicked`, `filled`, `focused`, `value`, `readable`, `blurred`, `set`,
`removed`, `deleted`, `cleared`, `items`, `cleared_count`, `requested_count`,
`state`, `attribute_found`, `requested_value`, `network_idle`, `quiet_ms`,
`submitted`, `hovered`, `pressed`, and `navigation_requested` before assuming
the page changed.

For page work, choose actions in this order:

1. Inspect with `snapshot`, then `interactive-snapshot` when selectors or roles
   are unclear.
2. Prefer semantic actions: `click-role` for known roles/names, `click-text` for
   visible text, and `fill-label` for labeled form fields.
3. Use selector actions when a stable selector is known: `exists`, `count`,
   `wait-count`, `query`, `get-attribute`, `wait-attribute`, `wait-text`,
   `get-text`, `wait-selector`, `click`, `type`, `focus`, `get-value`,
   `wait-value`, `blur`, `clear`, `submit`, `select-option`, `check`, and
   `uncheck`.
4. Use `reload`, `go-back`, `go-forward`, `wait-url`, `wait-load-state`, and
   `wait-network-idle` for navigation and async refresh flows.
5. Use `storage-get`, `storage-set`, `storage-remove`, and `storage-clear` for
   localStorage/sessionStorage state instead of writing storage JavaScript. Use
   `wait-storage` after actions expected to create, update, or remove keys.
6. Use `cookie-get`, `cookie-set`, `cookie-delete`, and `cookie-clear` for
   document.cookie-visible cookies. Use `wait-cookie` after consent/login flows;
   do not expect HttpOnly cookies here.
7. Use `scroll`, `hover`, or `press` for viewport, menu, and keyboard flows.
8. Use `eval` only for page-local work not covered by a first-class action, and
   keep the expression small.
9. If `result.found`, `result.exists`, `result.clicked`, or `result.filled` is
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
3. Navigate page history or async refresh: use `reload`, `go-back`, or
   `go-forward`, then confirm with `wait-url`, `wait-load-state`,
   `wait-network-idle`, `wait-text`, or `snapshot`.
4. Open menus or keyboard flows: use `focus`, `hover` for menus, `press` for
   shortcuts or Enter/Escape, and `blur` for focus-driven validation, then
   inspect again with `interactive-snapshot`.
5. Read page results: use `wait-count` for dynamic lists, `wait-attribute` for
   DOM state changes, `get-text` for a known selector; use `snapshot` when the
   page structure or selector is unknown; use `wait-text` before reading
   dynamic results.
6. Adjust browser state: use `storage-get` for local/session storage,
   `storage-set` for feature flags or onboarding state, and `storage-remove` or
   `storage-clear --prefix <prefix>` for targeted cleanup; use `wait-storage`
   when the page updates keys asynchronously. Use `cookie-get`, `cookie-set`,
   `cookie-delete`, or `cookie-clear` for document.cookie-visible cookies such
   as consent or non-HttpOnly flags, and `wait-cookie` when cookie changes are
   async.
7. Debug selectors: use `count`, `query`, and `get-attribute` before `eval`;
   use `wait-count` or `wait-attribute` for async DOM changes.
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
Failure messages and payloads mask `api_key`, token-like query parameters, and
the current `LEXMOUNT_API_KEY` value.
If `error` is `argument_error`, read the JSON `usage` field and rerun a
corrected command; do not parse stderr.
For `auth`, report credential presence, missing variables, and next steps; do
not report API key values. For `auth export-env`, use placeholders or masked
commands unless the user explicitly asked to reveal secrets locally.
For `doctor`, inspect `checks` and report failed check names without revealing
API keys. When a check includes `fix`, use its `commands`, `env`, and
`guidance` fields as the repair workflow.
