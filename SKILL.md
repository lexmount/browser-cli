---
name: browser-cli
description: Operate Lexmount remote browsers with browser-cli. Use when Codex or another agent needs to create, list, inspect, keep alive, or close browser sessions; manage persistent contexts, pick reusable contexts, or detect locked contexts; guide authentication with auth status/token-info/refresh/logout/export-env/login; verify installation, environment, and API connectivity with doctor; discover installed commands with commands; open pages, read page info, wait for selectors/states/roles/URLs/load/network/text/form values/dialogs/frames/console entries/fetch-XHR entries, click/type/fill/select/check/hover/press/scroll, inspect/query forms/links/tables/lists/text/dialogs/frames/performance/network/console/outlines/accessibility/interactive elements, manage storage/cookies, navigate history, screenshot, evaluate JavaScript, snapshot pages, or verify credentials without custom Playwright.
---

# browser-cli

Use `browser-cli` as the primary interface for Lexmount browser automation.
Prefer CLI commands and JSON output over importing Python internals or writing
ad hoc Playwright scripts.

## Setup

Check that the CLI is available:

```bash
browser-cli --help
browser-cli commands --names-only
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
browser-cli auth status --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth token-info --required-scope browser:actions
browser-cli auth refresh --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth logout --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth login
browser-cli auth login --open
browser-cli auth login --device-code
browser-cli auth export-env
```

When `auth login` returns `handoff`, use it as the setup contract: open
`connect_from_codex_url` or `login_url`, follow `copyable_commands`, require the
listed `local_env` variables in the user's local shell, and run the
`verification.doctor_command`. Use `browser-cli auth login --open` or
`handoff.open_command` only when the user wants the local browser opened; then
inspect `open_result`. Follow `secret_policy`: never paste `LEXMOUNT_API_KEY`,
revealed export output, or full direct URLs into chat.
If the user asks for device-code login, run `browser-cli auth login --device-code`
and parse `available`, `reason`, `device_code.required_endpoints`, and
`fallback_handoff`; while `available` is false, guide the user through the
manual env fallback.

`auth export-env` prints placeholders by default. With `--from-current`, it
still masks `LEXMOUNT_API_KEY` unless `--reveal-secrets` is explicitly used in
a trusted local terminal.

`auth status` reports `auth_source`, `runtime_auth_usable`, and safe
`device_token` metadata when a local scoped-token credentials file exists or
`--credentials-file` is passed. Use `auth token-info --required-scope <scope>`
to check scoped-token coverage. Use `auth refresh --credentials-file <path>` to
inspect `refresh_needed`, `has_refresh_token`, `refresh_available`, `refreshed`,
and `reason`; remote refresh is pending until browser.lexmount.cn exposes the
refresh API. Use `auth logout --credentials-file <path>` to remove local
device-token metadata without changing environment variables; `--revoke` only
reports remote revoke pending until browser.lexmount.cn exposes the revoke API.
These commands never report access or refresh token values. Until bearer-token
runtime support lands, continue to require env API-key credentials for browser
actions when `runtime_auth_usable` is false.

After credentials are configured, run:

```bash
browser-cli doctor --json
browser-cli doctor --smoke-session
```

Run `browser-cli doctor --json` before the first browser action in a thread,
after credential changes, or when a session/context/action command fails for an
unclear reason. Use `browser-cli doctor --smoke-session` only when you need
proof that credentials can create and close a temporary browser session. `--json`
is accepted as a no-op compatibility flag at the top level and after
subcommands; browser-cli output is always JSON. Parse the JSON before deciding
what to do:

- `ok: true` and `failed: 0`: continue with browser work.
- `ready_for_browser_actions: true`: browser sessions/actions can be attempted.
- `browser_smoke_session` with `status: "pass"`: a temporary browser session was
  created and closed.
- `browser_smoke_session` with `status: "fail"`: follow its `fix` commands,
  especially a manual `session close` command when `created` is true and
  `closed` is false.
- `command_catalog` with `status: "warn"`: inspect
  `missing_required_commands` and follow its `fix` guidance before relying on
  the full Skill workflow.
- `repair_plan`: prefer its aggregated `commands`, `env`, `guidance`, and
  `fixes` when explaining setup repair steps.
- `warnings > 0` or a check with `status: "warn"`: continue only after
  reporting warning check names and any `fix` guidance; warnings usually mean
  local installation/PATH hygiene rather than unusable credentials.
- `ok: false`: stop before creating sessions, inspect `checks` with
  `status: "fail"`, and follow each check's `fix` object when present.
- `api_connectivity` with `status: "skipped"`: do not treat live API access as
  verified.

Use `browser-cli doctor --skip-api` only for offline setup checks or when the
user explicitly asks to avoid a live API call. Do not treat a skipped API check
as proof that browser sessions will work.

Run `browser-cli commands --names-only` or `browser-cli commands --group action`
when the installed CLI version is uncertain or before writing custom JavaScript.
Use the catalog's `browser_target.exactly_one_of`, `required_options`,
`required_one_of`, `json_output`, `secret_policy`, and `agent_entrypoints`
fields instead of parsing `--help` text.

## Workflow

If setup is uncertain, run `browser-cli auth status`, then `browser-cli doctor --json`
before creating a session. If credentials are missing, run
`browser-cli auth login` and guide the user to set local environment variables.

For a one-off task:

```bash
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
browser-cli session create --context-metadata-json '{"purpose":"codex-login"}' --create-context-if-missing --context-mode read_write
browser-cli context pick --metadata-json '{"purpose":"codex-login"}' --create-if-missing --dry-run
```

Use `read_write` for login/setup work that should update cookies or storage. Use
`read_only` when inspecting an existing logged-in state. Before deleting a context,
confirm that the task no longer needs its login state.

Parse `context_reuse` from the session result. Reuse only when
`context_reuse.selected` is true. Prefer `availability` over raw status strings:
`available` can be reused, `locked` means busy, and `unavailable` needs a
different context. If candidates include `locked: true`, report that a busy
context was skipped. Inspect `selection_summary` for `locked_matches`,
`metadata_mismatches`, `reusable_matches`, and `would_create`. Use
`context pick --metadata-json <json> --dry-run` before creating a session when
you need to explain context reuse or avoid mutating persistent login state. Use
`context status --context-id <context_id>` before reuse when the context id came
from older notes.

If a command fails, parse the JSON error first. For configuration or credential
errors, stop browser work and guide the user to configure local environment
variables. For missing selectors, take a fresh snapshot or screenshot before
choosing another selector.

Write custom Playwright only when the CLI cannot express the task and explain
why the CLI was insufficient.

Always close sessions created for temporary automation unless the user asks to
keep them open. Always close temporary sessions.

## Commands

Authentication:

```bash
browser-cli auth status
browser-cli auth status --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth token-info --required-scope browser:actions
browser-cli auth refresh --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth logout --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth login
browser-cli auth login --open
browser-cli auth login --device-code
browser-cli auth export-env
browser-cli auth export-env --from-current --include-base-url
```

Diagnostics:

```bash
browser-cli commands
browser-cli commands --names-only
browser-cli commands --group action
browser-cli doctor
browser-cli doctor --json
browser-cli doctor --smoke-session
browser-cli doctor --skip-api
```

Session lifecycle:

```bash
browser-cli session create
browser-cli session create --context-metadata-json '{"purpose":"codex-login"}' --create-context-if-missing
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
browser-cli context pick --metadata-json '{"purpose":"codex-login"}' --create-if-missing --dry-run
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
browser-cli action page-info --session-id <session_id>
browser-cli action reload --session-id <session_id>
browser-cli action go-back --session-id <session_id>
browser-cli action go-forward --session-id <session_id>
browser-cli action wait-url --session-id <session_id> --url /dashboard
browser-cli action wait-title --session-id <session_id> --title Dashboard --match contains
browser-cli action wait-load-state --session-id <session_id> --state complete
browser-cli action wait-network-idle --session-id <session_id> --idle-ms 500
browser-cli action get-text --session-id <session_id> --selector "main"
browser-cli action exists --session-id <session_id> --selector "button"
browser-cli action count --session-id <session_id> --selector ".item"
browser-cli action wait-count --session-id <session_id> --selector ".item" --count 3 --comparison gte
browser-cli action wait-state --session-id <session_id> --selector "button" --state enabled
browser-cli action query --session-id <session_id> --selector ".item" --max-nodes 20
browser-cli action get-attribute --session-id <session_id> --selector "a" --name href
browser-cli action wait-attribute --session-id <session_id> --selector "button" --name aria-busy --state absent
browser-cli action wait-text --session-id <session_id> --text "Ready" --selector "main"
browser-cli action wait-text --session-id <session_id> --text "Loading" --state absent
browser-cli action wait-role --session-id <session_id> --role button --name "Submit"
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
browser-cli action set-value --session-id <session_id> --selector "input[name=q]" --value "query"
browser-cli action set-file-input --session-id <session_id> --selector "input[type=file]" --file ./avatar.png
browser-cli action dispatch-event --session-id <session_id> --selector "input[name=q]" --event input --event change
browser-cli action submit --session-id <session_id> --selector "form"
browser-cli action scroll --session-id <session_id> --y 600
browser-cli action scroll-into-view --session-id <session_id> --selector "button"
browser-cli action bounding-box --session-id <session_id> --selector "button"
browser-cli action inspect --session-id <session_id> --selector "button"
browser-cli action select-option --session-id <session_id> --selector "select" --value pro
browser-cli action select-label --session-id <session_id> --label "Plan" --option-label "Pro"
browser-cli action check --session-id <session_id> --selector "input[type=checkbox]"
browser-cli action uncheck --session-id <session_id> --selector "input[type=checkbox]"
browser-cli action check-label --session-id <session_id> --label "Remember me"
browser-cli action uncheck-label --session-id <session_id> --label "Remember me"
browser-cli action hover --session-id <session_id> --selector ".menu"
browser-cli action press --session-id <session_id> --selector "input[name=q]" --key Enter
browser-cli action press-key --session-id <session_id> --key Escape
browser-cli action click-text --session-id <session_id> --text "Submit"
browser-cli action click-role --session-id <session_id> --role button --name "Submit"
browser-cli action click-index --session-id <session_id> --selector ".item button" --index 2
browser-cli action fill-label --session-id <session_id> --label "Email" --text "me@example.com"
browser-cli action link-snapshot --session-id <session_id> --selector "main" --max-nodes 50
browser-cli action table-snapshot --session-id <session_id> --selector ".report" --max-rows 50 --max-cells 20
browser-cli action list-snapshot --session-id <session_id> --selector ".results" --max-items 50
browser-cli action text-snapshot --session-id <session_id> --selector "main" --max-nodes 50 --max-chars 500
browser-cli action dialog-snapshot --session-id <session_id> --max-nodes 20 --max-controls 30
browser-cli action wait-dialog --session-id <session_id> --text "Confirm" --modal-only
browser-cli action frame-snapshot --session-id <session_id> --selector "main" --max-nodes 20 --max-chars 500
browser-cli action wait-frame --session-id <session_id> --url "/checkout" --readable-only
browser-cli action performance-snapshot --session-id <session_id> --max-resources 50 --min-duration-ms 0
browser-cli action network-snapshot --session-id <session_id> --max-entries 50
browser-cli action wait-network --session-id <session_id> --url /api/save --method POST --status 201
browser-cli action console-snapshot --session-id <session_id> --max-entries 50
browser-cli action wait-console --session-id <session_id> --source pageerror --level error --timeout-ms 5000
browser-cli action outline-snapshot --session-id <session_id> --selector "main" --max-nodes 50
browser-cli action form-snapshot --session-id <session_id> --selector "form" --max-nodes 50
browser-cli action accessibility-snapshot --session-id <session_id> --max-nodes 100
browser-cli action interactive-snapshot --session-id <session_id>
```

Prefer these built-in actions over writing custom JavaScript. `page-info`, `reload`,
`go-back`, `go-forward`, `wait-url`, `wait-title`, `wait-load-state`, `wait-network-idle`,
`get-text`, `exists`, `count`, `query`, `get-attribute`, `wait-count`,
`wait-state`, `wait-attribute`, `wait-text`, `wait-role`, `focus`, `get-value`, `wait-value`, `blur`,
`storage-get`, `storage-set`, `storage-remove`, `storage-clear`,
`wait-storage`, `cookie-get`, `cookie-set`, `cookie-delete`, `cookie-clear`,
`wait-cookie`, `clear`, `set-value`, `set-file-input`, `dispatch-event`,
`submit`, `scroll`, `scroll-into-view`, `bounding-box`, `inspect`,
`select-option`, `select-label`, `check`, `uncheck`, `check-label`,
`uncheck-label`, `hover`, `press`, and `press-key` plus `click-text`, `click-role`,
`click-index`, `fill-label`, `link-snapshot`, `table-snapshot`, `list-snapshot`, `text-snapshot`, `dialog-snapshot`, `wait-dialog`, `frame-snapshot`, `wait-frame`, `performance-snapshot`, `network-snapshot`, `wait-network`, `console-snapshot`, `wait-console`, `outline-snapshot`, `form-snapshot`,
`accessibility-snapshot`, and `interactive-snapshot` are DOM/eval backed, so always parse their structured
`result` fields such as `found`, `exists`, `count`, `checked`, `selected`,
`clicked`, `filled`, `focused`, `value`, `readable`, `blurred`, `set`,
`removed`, `deleted`, `cleared`, `items`, `cleared_count`, `requested_count`,
`state`, `matched`, `state_values`, `attribute_found`, `requested_value`,
`network_idle`, `quiet_ms`, `submitted`, `hovered`, `pressed`, `dispatched`,
`dispatched_events`, `fields`, `value_masked`, `file_input`, `file_count`,
`requested_files`, `bounding_box`, `in_viewport`, `index`, `attributes`,
`html_truncated`, `total_candidate_count`, `requested_option_label`, `option_found`, `option_label`,
`requested_checked`, `previous_checked`, `changed`, `links`, `link_count`,
`href`, `href_masked`, `absolute_url`, `absolute_url_masked`, `same_origin`,
`external`, `download`, `tables`, `table_count`, `headers`, `rows`, `cells`,
`row_count`, `cell_count`, `lists`, `list_count`, `items`, `item_count`,
`expanded`, `texts`, `text_count`, `text_length`, `text_truncated`,
`aria_live`, `dialogs`, `dialog_count`, `total_dialog_count`, `requested_text`,
`modal_only`, `controls`, `control_count`,
`controls_truncated`, `modal`, `frames`, `frame_count`, `total_frame_count`,
`src`, `src_masked`, `frame_url`, `frame_url_masked`, `readable`,
`readable_only`, `same_origin_only`, `text_match`, `read_error`, `navigation`,
`resources`, `resource_count`, `initiator_type`, `initiator_types`, `duration`,
`transfer_size`, `response_status`, `entries`, `entry_count`, `matched_count`,
`buffered_count`, `source`, `level`, `method`, `requested_method`, `status`,
`ok`, `failed`, `failed_only`, `request_has_body`, `duration_ms`,
`text_masked`, `filename_masked`, `url_masked`, `timed_out`,
`requested_url`, `url_match`, `requested_source`, `requested_status`,
`requested_level`, `after_index`, `headings`, `landmarks`, `outline_count`,
`heading_count`, `landmark_count`, `node_type`, `level`, `ready_state`,
`visibility_state`, `viewport`, `scroll`, `body_text_length`, `html_length`,
`language`, `referrer`, `requested_title`, `case_sensitive`, `code`, `target`,
`target_info`, `modifiers`, `events`, `keydown_accepted`, and `navigation_requested`
before assuming the page changed.
For DOM/form actions, fields that look like password, token, credential, secret,
authorization, or API-key controls are masked by default. If `value`,
`previous_value`, `requested_value`, or `text` is `***`, inspect
`value_masked`, `previous_value_masked`, `requested_value_masked`,
`text_masked`, and related `*_length` fields; do not ask the user to paste the
real value into chat.
For `link-snapshot`, URL query parameters that look like API keys, access
tokens, authorization codes, passwords, or secrets are masked by default. Use
`href_masked` and `absolute_url_masked` before copying or reporting URLs.
`table-snapshot`, `list-snapshot`, `dialog-snapshot`, `wait-dialog`, `frame-snapshot`, `wait-frame`, and
`performance-snapshot` use the same URL masking for links, frame URLs, and
performance resource URLs found inside table cells, list items, dialog controls,
frame metadata, or timing entries.
`network-snapshot` and `wait-network` mask fetch/XHR URLs and do not capture
request or response bodies; use `request_has_body` only as a boolean hint.
`console-snapshot` and `wait-console` mask token-like key/value text in captured
console/page error entries and the reported page URL.
For `page-info`, parse `ready_state`, `visibility_state`, `viewport`, `scroll`,
`body_text_length`, `html_length`, `language`, and `referrer` before taking a
larger `snapshot`.

For page work, choose actions in this order:

1. Inspect with `snapshot`, then `interactive-snapshot` when selectors or roles
   are unclear; use `outline-snapshot` for page structure; use `form-snapshot`
   before filling complex forms; use `list-snapshot` before choosing from menus,
   search results, listboxes, or task lists; use `text-snapshot` for bounded
   visible text, alerts, and status messages; use `wait-dialog` or
   `dialog-snapshot` for modals, alert dialogs, cookie banners, and confirmation
   prompts; use `wait-frame` or `frame-snapshot` before frame-related JavaScript
   or when content appears embedded.
2. Prefer semantic actions: `wait-role` for async roles/names, `click-role` for known roles/names, `click-text` for
   visible text, `click-index` for a chosen repeated selector match,
   `link-snapshot` for choosing or reporting navigation URLs, `list-snapshot`
   for reading list/menu item state, `fill-label` for
   labeled text fields, `select-label` for labeled native selects, and
   `check-label` for labeled checkbox or switch controls.
3. Use selector actions when a stable selector is known: `exists`, `count`,
   `wait-count`, `wait-state`, `query`, `inspect`, `get-attribute`, `wait-attribute`, `wait-text`,
   `get-text`, `wait-selector`, `click`, `type`, `focus`, `get-value`,
   `wait-value`, `blur`, `clear`, `set-value`, `dispatch-event`, `submit`,
   `select-option`, `check`, and `uncheck`.
4. Use `page-info`, `reload`, `go-back`, `go-forward`, `wait-url`,
   `wait-title`, `wait-load-state`, `wait-network-idle`, and
   `performance-snapshot` for navigation and async refresh flows.
   For fetch/XHR debugging, run `network-snapshot --install-only`, trigger the
   behavior, then run `network-snapshot` or `wait-network` and parse `entries`.
   For runtime errors, run `console-snapshot --install-only`, trigger the
   behavior, then run `console-snapshot` or `wait-console` and parse `entries`.
5. Use `storage-get`, `storage-set`, `storage-remove`, and `storage-clear` for
   localStorage/sessionStorage state instead of writing storage JavaScript. Use
   `wait-storage` after actions expected to create, update, or remove keys.
6. Use `cookie-get`, `cookie-set`, `cookie-delete`, and `cookie-clear` for
   document.cookie-visible cookies. Use `wait-cookie` after consent/login flows;
   do not expect HttpOnly cookies here.
7. Use `scroll`, `scroll-into-view`, `bounding-box`, `inspect`, `hover`,
   selector-scoped `press`, active/global `press-key`, or `dispatch-event` for
   viewport, menu, keyboard, geometry, and event-triggered UI flows.
8. Use `eval` only for page-local work not covered by a first-class action, and
   keep the expression small.
9. If `result.found`, `result.exists`, `result.clicked`, or `result.filled` is
   false, inspect again before trying a different action. For form state, parse
   `result.value` and `result.readable` before deciding whether to type again.

Common task recipes:

1. Fill and submit a form: run `form-snapshot` or `interactive-snapshot`, use
   `fill-label` for labeled fields, `set-value` for stable selectors, and
   `set-file-input` for upload controls; `clear` before replacement text when
   needed, use `get-value` or `wait-value` to confirm form state, use
   `blur` for focus-driven validation, use
   `select-label` for labeled selects, `select-option` or `check` for stable
   selector controls, prefer `check-label` for labeled controls, use
   `wait-state --state enabled` or `wait-role` for async submit buttons, use
   `dispatch-event --event input --event change` when the app needs explicit
   events, then use `submit`,
   `click-role --role button --name <text>` or `click-text`.
2. Click a visible control: use `wait-role` when the control appears asynchronously,
   use `link-snapshot` when the task is to choose, inspect, or report navigation URLs,
   use `list-snapshot` before choosing from menus, listboxes, task lists, or search results,
   prefer `click-role`, then `click-text`, then `scroll-into-view` and selector
   `click` after `exists`, `inspect`, or `bounding-box` confirms a stable
   selector. For repeated matches, run `query` and then `click-index --index <n>`.
3. Navigate page history or async refresh: use `reload`, `go-back`, or
   `go-forward`, then confirm with `page-info`, `wait-url`, `wait-title`,
   `wait-load-state`, `wait-network-idle`, `performance-snapshot`, `wait-text`,
   or `snapshot`.
4. Diagnose fetch/XHR calls: run `network-snapshot --install-only`, trigger the
   suspected action, read `network-snapshot` or wait with `wait-network`, and
   parse `entries`, `method`, `status`, `ok`, `failed`, `duration_ms`, and
   masked URLs; use `--failed-only` when looking for transport failures.
5. Capture runtime errors: run `console-snapshot --install-only`, trigger the
   suspected action, read `console-snapshot` or wait with `wait-console`, then
   use `text-snapshot`, `wait-dialog`, `dialog-snapshot`, `wait-frame`, or
   `inspect` to correlate visible state with JS errors.
6. Open menus or keyboard flows: use `focus`, `hover` for menus, `press` for
   selector-scoped keys, `press-key` for active/global shortcuts such as
   Enter/Escape, `dispatch-event` for explicit DOM events, and
   `blur` for focus-driven validation, then inspect again with
   `interactive-snapshot`. For modal dialogs, alert dialogs, cookie banners, or
   confirmation prompts, run `wait-dialog` when the dialog appears asynchronously,
   otherwise run `dialog-snapshot`, choose from `controls`, then use
   `click-role`, `click-text`, or `click-index`. For iframe or embedded app
   issues, run `wait-frame` when the frame appears asynchronously, otherwise run
   `frame-snapshot` and parse `readable`, `same_origin`, `frame_url`, and
   `read_error` before deciding whether direct DOM inspection is possible.
7. Read page results: use `page-info` for URL/title/readyState/viewport checks,
   `wait-title` for async title changes, `wait-count` for dynamic lists,
   `list-snapshot` for menu/listbox/search-result/task-list content,
   `text-snapshot` for visible paragraphs, alerts, status messages, and bounded readable text,
   `table-snapshot` for HTML or ARIA table/report data,
   `outline-snapshot` for headings and landmarks,
   `wait-attribute` for DOM attributes, `wait-state` for
   enabled/visible/checked/focused states, `get-text` for a known selector; use
   `snapshot` when the page structure or selector is unknown; use `wait-text`
   or `wait-role` before reading dynamic results, and use
   `wait-text --state absent` when loading, toast, or error text should disappear.
8. Adjust browser state: use `storage-get` for local/session storage,
   `storage-set` for feature flags or onboarding state, and `storage-remove` or
   `storage-clear --prefix <prefix>` for targeted cleanup; use `wait-storage`
   when the page updates keys asynchronously. Use `cookie-get`, `cookie-set`,
   `cookie-delete`, or `cookie-clear` for document.cookie-visible cookies such
   as consent or non-HttpOnly flags, and `wait-cookie` when cookie changes are
   async.
7. Debug selectors: use `count`, `query`, `inspect`, and `get-attribute` before
   `eval`; use `inspect` for `state.disabled`, `state.readonly`, masked
   `value`, `attributes`, and `in_viewport`; use `wait-count`, `wait-state`,
   or `wait-attribute` for async DOM changes.
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
`error`, `message`, and command-specific fields. Do not log revealed API keys.
It is safe to include `--json` at the top level or after subcommands because it
does not change output.
For `commands`, use the parser-backed catalog to discover installed commands and
options before guessing. Prefer `--names-only` for quick availability checks and
`--group action` before choosing browser actions.
Do not paste API keys, Project IDs, or full direct connect URLs into chat, docs,
commits, screenshots, or test fixtures. By default, browser direct URLs are
masked. Use reveal flags only for local debugging in a trusted shell.
Failure messages and payloads mask `api_key`, token-like query parameters, and
the current `LEXMOUNT_API_KEY` value.
If `error` is `argument_error`, read the JSON `usage` field and rerun a
corrected command; do not parse stderr.
For `auth`, report credential presence, missing variables, and next steps; do
not report API key values. For `auth login`, prefer the `handoff` object's
`copyable_commands`, `open_command`, `local_env`, `verification`, and
`secret_policy` fields. If `--open` was used, inspect `open_result`; if
`opened` is false, show the returned URL or fallback login guidance without
blocking on the local browser.
For device-token metadata in `auth status`, `auth token-info`, `auth refresh`,
`auth logout`, or `doctor`, report `auth_source`, `runtime_auth_usable`,
`device_token.valid`, `device_token.expired`, `device_token.refresh_needed`,
`device_token.scopes`, and `scope_check`; do not report token values. For
`auth refresh`, report `refresh_needed`, `has_refresh_token`,
`refresh_available`, `refreshed`, and `reason`; it does not call a remote
refresh endpoint yet. For `auth logout`, report `deleted`, `present_before`,
`present_after`,
`revoke_requested`, `revoke_available`, and `warnings`; it does not unset env
vars. Do not start browser actions from a device token while
`runtime_auth_usable` is false.
For `auth export-env`, use placeholders or masked commands unless the user
explicitly asked to reveal secrets locally.
For `doctor`, inspect `ready_for_browser_actions`, `failed_checks`,
`warning_checks`, `skipped_checks`, and `repair_plan` first. Report failed or
warning check names without revealing API keys. If `browser_smoke_session`
exists, report whether it created and closed the temporary session and follow
its manual close command when cleanup failed. Prefer `repair_plan.commands`,
`repair_plan.env`, and `repair_plan.guidance`; fall back to per-check `fix`
objects only when needed.
