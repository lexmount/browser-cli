# Browser Action Playbook

Load this reference when choosing browser actions, filling forms, diagnosing
runtime or network issues, handling frames/dialogs, or deciding whether custom
JavaScript is necessary.

## Contents

- Action command examples
- Structured result fields and masking
- Action selection order
- Common task recipes
- Target contract

## Action Command Examples

Start from the installed command catalog before guessing:

```bash
browser-cli commands --group action
browser-cli commands --group action --names-only
browser-cli commands --workflow form_interaction
browser-cli commands --workflow interactive_targeting
browser-cli commands --workflow page_diagnostics
browser-cli action guide --names-only
browser-cli action guide --task form_interaction
browser-cli action guide --task interactive_targeting
browser-cli action guide --task page_diagnostics
```

`action guide` is the compact machine-readable route for a known browser task;
use it before loading the longer playbook when the task category is clear.

Common actions:

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
browser-cli action get-text-role --session-id <session_id> --role heading --name "Welcome"
browser-cli action exists --session-id <session_id> --selector "button"
browser-cli action exists-role --session-id <session_id> --role button --name "Submit"
browser-cli action count --session-id <session_id> --selector ".item"
browser-cli action wait-count --session-id <session_id> --selector ".item" --count 3 --comparison gte
browser-cli action wait-state --session-id <session_id> --selector "button" --state enabled
browser-cli action wait-state-role --session-id <session_id> --role button --name "Submit" --state enabled
browser-cli action query --session-id <session_id> --selector ".item" --max-nodes 20
browser-cli action inspect --session-id <session_id> --selector "button"
browser-cli action get-attribute --session-id <session_id> --selector "a" --name href
browser-cli action wait-attribute --session-id <session_id> --selector "button" --name aria-busy --state absent
browser-cli action wait-text --session-id <session_id> --text "Ready" --selector "main"
browser-cli action wait-text --session-id <session_id> --text "Loading" --state absent
browser-cli action wait-role --session-id <session_id> --role button --name "Submit"
browser-cli action focus --session-id <session_id> --selector "input[name=q]"
browser-cli action focus-role --session-id <session_id> --role textbox --name "Search"
browser-cli action get-value --session-id <session_id> --selector "input[name=q]"
browser-cli action get-value-role --session-id <session_id> --role textbox --name "Search"
browser-cli action wait-value --session-id <session_id> --selector "input[name=q]" --value "query"
browser-cli action wait-value-role --session-id <session_id> --role textbox --name "Search" --value "query"
browser-cli action blur --session-id <session_id> --selector "input[name=q]"
browser-cli action blur-role --session-id <session_id> --role textbox --name "Search"
browser-cli action clear --session-id <session_id> --selector "input[name=q]"
browser-cli action clear-role --session-id <session_id> --role textbox --name "Search"
browser-cli action set-value --session-id <session_id> --selector "input[name=q]" --value "query"
browser-cli action set-file-input --session-id <session_id> --selector "input[type=file]" --file ./avatar.png
browser-cli action dispatch-event --session-id <session_id> --selector "input[name=q]" --event input --event change
browser-cli action submit --session-id <session_id> --selector "form"
browser-cli action scroll --session-id <session_id> --y 600
browser-cli action scroll-into-view --session-id <session_id> --selector "button"
browser-cli action scroll-into-view-role --session-id <session_id> --role button --name "Submit"
browser-cli action bounding-box --session-id <session_id> --selector "button"
browser-cli action bounding-box-role --session-id <session_id> --role button --name "Submit"
browser-cli action select-option --session-id <session_id> --selector "select" --value pro
browser-cli action select-label --session-id <session_id> --label "Plan" --option-label "Pro"
browser-cli action select-role --session-id <session_id> --role combobox --name "Plan" --option-label "Pro"
browser-cli action check --session-id <session_id> --selector "input[type=checkbox]"
browser-cli action uncheck --session-id <session_id> --selector "input[type=checkbox]"
browser-cli action check-label --session-id <session_id> --label "Remember me"
browser-cli action uncheck-label --session-id <session_id> --label "Remember me"
browser-cli action check-role --session-id <session_id> --role checkbox --name "Remember me"
browser-cli action uncheck-role --session-id <session_id> --role checkbox --name "Remember me"
browser-cli action hover --session-id <session_id> --selector ".menu"
browser-cli action hover-role --session-id <session_id> --role button --name "Menu"
browser-cli action press --session-id <session_id> --selector "input[name=q]" --key Enter
browser-cli action press-role --session-id <session_id> --role textbox --name "Search" --key Enter
browser-cli action press-key --session-id <session_id> --key Escape
browser-cli action click-text --session-id <session_id> --text "Submit"
browser-cli action click-role --session-id <session_id> --role button --name "Submit"
browser-cli action click-index --session-id <session_id> --selector ".item button" --index 2
browser-cli action fill-label --session-id <session_id> --label "Email" --text "me@example.com"
browser-cli action fill-role --session-id <session_id> --role textbox --name "Email" --text "me@example.com"
```

Inspection and diagnostics:

```bash
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
browser-cli action interactive-only-snapshot --session-id <session_id>
```

Storage and cookies:

```bash
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
```

## Structured Results And Masking

Prefer built-in actions over writing custom JavaScript. `page-info`, `reload`,
`go-back`, `go-forward`, `wait-url`, `wait-title`, `wait-load-state`,
`wait-network-idle`, `get-text`, `get-text-role`, `exists`, `exists-role`, `count`, `query`, `inspect`,
`get-attribute`, `wait-count`, `wait-state`, `wait-state-role`, `wait-attribute`, `wait-text`,
`wait-role`, `focus`, `focus-role`, `get-value`, `get-value-role`, `wait-value`,
`wait-value-role`, `blur`, `blur-role`, `storage-get`,
`storage-set`, `storage-remove`, `storage-clear`, `wait-storage`, `cookie-get`,
`cookie-set`, `cookie-delete`, `cookie-clear`, `wait-cookie`, `clear`, `clear-role`,
`set-value`, `set-file-input`, `dispatch-event`, `submit`, `scroll`,
`scroll-into-view`, `scroll-into-view-role`, `bounding-box`, `bounding-box-role`, `select-option`, `select-label`, `select-role`, `check`,
`uncheck`, `check-label`, `check-role`, `uncheck-label`, `uncheck-role`, `hover`, `hover-role`, `press`, `press-role`, and `press-key`
plus `click-text`, `click-role`, `click-index`, `fill-label`, `fill-role`, `link-snapshot`,
`table-snapshot`, `list-snapshot`, `text-snapshot`, `dialog-snapshot`,
`wait-dialog`, `frame-snapshot`, `wait-frame`, `performance-snapshot`,
`network-snapshot`, `wait-network`, `console-snapshot`, `wait-console`,
`outline-snapshot`, `form-snapshot`, `accessibility-snapshot`,
`interactive-snapshot`, and its `interactive-only-snapshot` alias are
DOM/eval-backed, so always parse their structured `result` fields.

Important result fields include `found`, `exists`, `count`, `checkable`,
`checked`, `selectable`, `selected`, `clicked`, `filled`, `focused`, `value`, `readable`, `blurred`,
`set`, `removed`, `deleted`, `clearable`, `cleared`, `items`, `cleared_count`,
`requested_count`, `state`, `matched`, `role_found`, `state_values`, `attribute_found`,
`requested_value`, `network_idle`, `quiet_ms`, `submitted`, `hovered`,
`pressed`, `dispatched`, `dispatched_events`, `fields`, `value_masked`,
`file_input`, `file_count`, `requested_files`, `bounding_box`, `in_viewport`,
`index`, `attributes`, `html_truncated`, `candidate_count`, `candidates`,
`writable`, `total_candidate_count`,
`requested_option_label`, `option_found`, `option_label`, `requested_checked`,
`previous_checked`, `changed`, `links`, `link_count`, `href`, `href_masked`,
`absolute_url`, `absolute_url_masked`, `same_origin`, `external`, `download`,
`tables`, `table_count`, `headers`, `rows`, `cells`, `row_count`, `cell_count`,
`lists`, `list_count`, `item_count`, `expanded`, `texts`, `text_count`,
`text_length`, `text_truncated`, `aria_live`, `dialogs`, `dialog_count`,
`total_dialog_count`, `requested_text`, `modal_only`, `controls`,
`control_count`, `controls_truncated`, `modal`, `frames`, `frame_count`,
`total_frame_count`, `src`, `src_masked`, `frame_url`, `frame_url_masked`,
`readable_only`, `same_origin_only`, `text_match`, `read_error`, `navigation`,
`resources`, `resource_count`, `initiator_type`, `initiator_types`, `duration`,
`transfer_size`, `response_status`, `entries`, `entry_count`, `matched_count`,
`buffered_count`, `source`, `level`, `method`, `requested_method`, `status`,
`ok`, `failed`, `failed_only`, `request_has_body`, `duration_ms`,
`text_masked`, `filename_masked`, `url_masked`, `timed_out`, `requested_url`,
`url_match`, `requested_source`, `requested_status`, `requested_level`,
`after_index`, `headings`, `landmarks`, `outline_count`, `heading_count`,
`landmark_count`, `node_type`, `ready_state`, `visibility_state`, `viewport`,
`scroll`, `body_text_length`, `html_length`, `language`, `referrer`,
`requested_title`, `case_sensitive`, `code`, `target`, `target_info`,
`modifiers`, `events`, `keydown_accepted`, and `navigation_requested`.

For DOM/form actions, fields that look like password, token, credential,
secret, authorization, or API-key controls are masked by default. If `value`,
`previous_value`, `requested_value`, or `text` is `***`, inspect
`value_masked`, `previous_value_masked`, `requested_value_masked`,
`text_masked`, and related `*_length` fields; do not ask the user to paste the
real value into chat.

For `link-snapshot`, URL query parameters that look like API keys, access
tokens, authorization codes, passwords, or secrets are masked by default. Use
`href_masked` and `absolute_url_masked` before copying or reporting URLs.
`table-snapshot`, `list-snapshot`, `dialog-snapshot`, `wait-dialog`,
`frame-snapshot`, `wait-frame`, and `performance-snapshot` use the same URL
masking for links, frame URLs, and performance resource URLs found inside table
cells, list items, dialog controls, frame metadata, or timing entries.
`network-snapshot` and `wait-network` mask fetch/XHR URLs and do not capture
request or response bodies; use `request_has_body` only as a boolean hint.
`console-snapshot` and `wait-console` mask token-like key/value text in captured
console/page error entries and the reported page URL.

## Action Selection Order

1. Inspect with `snapshot`, then `interactive-snapshot` when selectors or roles
   are unclear; use `outline-snapshot` for page structure; use `form-snapshot`
   before filling complex forms; use `list-snapshot` before choosing from
   menus, search results, listboxes, or task lists; use `text-snapshot` for
   bounded visible text, alerts, and status messages; use `wait-dialog` or
   `dialog-snapshot` for modals, alert dialogs, cookie banners, and
   confirmation prompts; use `wait-frame` or `frame-snapshot` before
   frame-related JavaScript or when content appears embedded.
2. Prefer semantic actions: `wait-role` for async roles/names,
   `exists-role`, `get-text-role`, and `bounding-box-role` for semantic
   existence, text, or geometry checks, `click-role` for known roles/names,
   `click-text` for visible text, `click-index` for a chosen repeated selector
   match, `link-snapshot` for choosing or reporting navigation
   URLs, `list-snapshot` for reading list/menu item state, `fill-label` for
   labeled text fields, `fill-role` for writable role/name fields,
   `focus-role`, `blur-role`, and `clear-role` for role/name form controls,
   `select-label` or `select-role` for native selects, and
   `check-label`, `check-role`, or `uncheck-role` for checkbox or switch controls.
3. Use selector actions when a stable selector is known: `exists`, `count`,
   `wait-count`, `wait-state`, `query`, `inspect`, `get-attribute`,
   `wait-attribute`, `wait-text`, `get-text`, `wait-selector`, `click`, `type`,
   `focus`, `get-value`, `get-value-role`, `wait-value`, `wait-value-role`, `blur`, `clear`, `set-value`,
   `dispatch-event`, `submit`, `select-option`, `check`, and `uncheck`.
4. Use `page-info`, `reload`, `go-back`, `go-forward`, `wait-url`,
   `wait-title`, `wait-load-state`, `wait-network-idle`, and
   `performance-snapshot` for navigation and async refresh flows.
5. Use `network-snapshot --install-only`, reproduce, then read
   `network-snapshot` or `wait-network` for fetch/XHR debugging.
6. For runtime errors, run `console-snapshot --install-only`, reproduce, then
   read `console-snapshot` or `wait-console`.
7. Use `storage-get`, `storage-set`, `storage-remove`, and `storage-clear` for
   localStorage/sessionStorage state; use `wait-storage` after async changes.
8. Use `cookie-get`, `cookie-set`, `cookie-delete`, and `cookie-clear` for
   document.cookie-visible cookies; use `wait-cookie` after consent/login flows.
   Do not expect HttpOnly cookies here.
9. Use `scroll`, `scroll-into-view`, `bounding-box`, `inspect`, `hover`,
   selector-scoped `press`, active/global `press-key`, or `dispatch-event` for
   viewport, menu, keyboard, geometry, and event-triggered UI flows.
10. Use `eval` only for page-local work not covered by a first-class action,
    and keep the expression small.
11. If `result.found`, `result.exists`, `result.clicked`, or `result.filled` is
    false, inspect again before trying a different action. For form state, parse
    `result.value` and `result.readable` before deciding whether to type again.

## Common Task Recipes

1. Fill and submit a form: run `browser-cli commands --workflow
   form_interaction` and `browser-cli action guide --task form_interaction`,
   then run `form-snapshot` or `interactive-snapshot`, use `fill-label` for
   labeled fields, `fill-role` for accessible role/name textboxes,
   `set-value` for stable selectors, and `set-file-input` for upload controls;
   `clear-role` or `clear` before replacement text when needed, use
   `get-value-role`, `wait-value-role`, `get-value`, or `wait-value` to confirm form state, use `blur-role` or `blur` for
   focus-driven validation, use `select-label` or `select-role` for selects,
   `select-option` or `check` for stable selector controls, prefer
   `check-label`, `check-role`, or `uncheck-role` for semantic controls, use
   `wait-state-role --state enabled`, `wait-state --state enabled`, or
   `wait-role` for async submit buttons, use `dispatch-event --event input
   --event change` when the app needs explicit events, then use `submit`,
   `click-role --role button --name <text>` or `click-text`.
2. Click a visible control: run `browser-cli commands --workflow
   interactive_targeting` and
   `browser-cli action guide --task interactive_targeting`, use
   `interactive-snapshot` or `accessibility-snapshot` to choose a target, use
   `wait-role` when the
   control appears asynchronously, use `link-snapshot` when the task is to
   choose, inspect, or report navigation URLs, use `list-snapshot` before
   choosing from menus, listboxes, task lists, or search results, prefer
   `click-role`, then `click-text`, then `scroll-into-view`; use
   `exists-role`, `get-text-role`, or `bounding-box-role` before activating
   when role/name evidence needs confirmation, and selector
   `click` after `exists`, `inspect`, or `bounding-box` confirms a stable
   selector. For repeated matches, run `query` and then `click-index --index
   <n>`.
3. Navigate page history or async refresh: use `reload`, `go-back`, or
   `go-forward`, then confirm with `page-info`, `wait-url`, `wait-title`,
   `wait-load-state`, `wait-network-idle`, `performance-snapshot`, `wait-text`,
   or `snapshot`.
4. Diagnose fetch/XHR calls: run `browser-cli commands --workflow
   page_diagnostics` and `browser-cli action guide --task page_diagnostics`,
   then run `network-snapshot --install-only`, trigger the suspected action,
   read `network-snapshot` or wait with `wait-network`, and parse `entries`,
   `method`, `status`, `ok`, `failed`, `duration_ms`, and masked URLs; use
   `--failed-only` when looking for transport failures.
5. Capture runtime errors: run `browser-cli commands --workflow
   page_diagnostics` and `browser-cli action guide --task page_diagnostics`,
   then run `console-snapshot --install-only`, trigger the suspected action,
   read `console-snapshot` or wait with `wait-console`, then use
   `text-snapshot`, `wait-dialog`, `dialog-snapshot`, `wait-frame`, or
   `inspect` to correlate visible state with JS errors.
6. Open menus or keyboard flows: use `focus-role`, `hover-role`, `press-role`,
   or `scroll-into-view-role` when role/name is known; use `focus`, `hover`, or `press` for
   stable selector-scoped keys, `press-key` for active/global shortcuts such as
   Enter/Escape, `dispatch-event` for explicit DOM events, and `blur-role` or `blur` for
   focus-driven validation, then inspect again with `interactive-snapshot`. For
   modal dialogs, alert dialogs, cookie banners, or confirmation prompts, run
   `wait-dialog` when the dialog appears asynchronously, otherwise run
   `dialog-snapshot`, choose from `controls`, then use `click-role`,
   `click-text`, or `click-index`. For iframe or embedded app issues, run
   `wait-frame` when the frame appears asynchronously, otherwise run
   `frame-snapshot` and parse `readable`, `same_origin`, `frame_url`, and
   `read_error` before deciding whether direct DOM inspection is possible.
7. Read page results: use `page-info` for URL/title/readyState/viewport checks,
   `wait-title` for async title changes, `wait-count` for dynamic lists,
   `list-snapshot` for menu/listbox/search-result/task-list content,
   `text-snapshot` for visible paragraphs, alerts, status messages, and bounded
   readable text, `table-snapshot` for HTML or ARIA table/report data,
   `outline-snapshot` for headings and landmarks, `wait-attribute` for DOM
   attributes, `wait-state-role` for semantic enabled/visible/checked/focused
   states, `wait-state` for selector states, `get-text-role` for semantic text
   checks, and `get-text` for a known selector. Use `snapshot` when the page structure or
   selector is unknown; use `wait-text` or `wait-role` before reading dynamic
   results, and use `wait-text --state absent` when loading, toast, or error
   text should disappear.
8. Adjust browser state: use `storage-get` for local/session storage,
   `storage-set` for feature flags or onboarding state, and `storage-remove` or
   `storage-clear --prefix <prefix>` for targeted cleanup; use `wait-storage`
   when the page updates keys asynchronously. Use `cookie-get`, `cookie-set`,
   `cookie-delete`, or `cookie-clear` for document.cookie-visible cookies such
   as consent or non-HttpOnly flags, and `wait-cookie` when cookie changes are
   async.
9. Debug selectors: use `count`, `query`, `inspect`, and `get-attribute` before
   `eval`; use `inspect` for `state.disabled`, `state.readonly`, masked
   `value`, `attributes`, and `in_viewport`; use `wait-count`, `wait-state`, or
   `wait-attribute` for async DOM changes.
10. Capture final evidence: use `screenshot` after the action sequence and
    close the session unless the user asks to keep it open.

## Target Contract

Each action must use exactly one target:

```bash
--session-id <session_id>
--connect-url <cdp_websocket_url>
--direct-url
```

Prefer `--session-id`. Use `--direct-url` only when the user explicitly wants
the shared direct websocket path.
