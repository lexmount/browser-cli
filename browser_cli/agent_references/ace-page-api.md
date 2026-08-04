# ACE Page API

Use `scripts/cdp.py <command>` with the explicit daemon-local `sessionId`
returned by `attach TARGET_ID`. Prefer high-level `navigate`, `content`, and
`action`. Use raw ACE `Page` methods only when the high-level workflow is
unsuitable, and never create a separate CDP connection.

`sessions` and `attach` select a connection. For Lexmount, pass
`--lexmount-session-id ID` to both commands; the client obtains the revealed
CDP URL from `browser-cli` internally and never prints or persists it. An
explicit `--websocket-url URL` connects another already-running Chromium
browser directly. Omitting both selectors chooses `127.0.0.1:9222`.

A different selection restarts the daemon and invalidates every existing ACE
`sessionId`. Subsequent `detach`, `navigate`, `call`, `content`, and `action`
commands omit endpoint selection and reuse the active daemon connection. Never
invoke the daemon directly or start, restart, or close the browser.

## Contents

- [Lexmount Session Initialization](#lexmount-session-initialization)
- [High-level `navigate`](#high-level-navigate)
- [High-level `content`](#high-level-content)
- [High-level `action`](#high-level-action)
- [`Page.getAIPageContent`](#pagegetaipagecontent)
- [`Page.performDOMAction`](#pageperformdomaction)
- [`Page.getAIPageActionChanges`](#pagegetaipageactionchanges)

## Lexmount Session Initialization

Create or select an active Lexmount session through browser-cli. Keep its
`session_id` separate from a CDP `targetId` and an ACE `sessionId`:

```text
browser-cli session create
cdp.py --lexmount-session-id LEXMOUNT_SESSION_ID sessions
cdp.py --lexmount-session-id LEXMOUNT_SESSION_ID attach TARGET_ID
```

`--lexmount-session-id` is mutually exclusive with `--websocket-url` and valid
only for `sessions` and `attach`. It invokes `browser-cli session get
--session-id ID --reveal-connect-url` locally, requires an active matching
session and a valid browser-level `ws://` or `wss://` URL, and transfers that
URL to the daemon over a private inherited pipe. The real URL must not appear
in stdout, stderr, logs, state files, command arguments, chat, or committed
fixtures.

If browser-cli is missing, returns invalid JSON, reports a different or
inactive session, omits the revealed URL, or ACE cannot attach, stop. Do not
fall back to a browser-cli page action and do not replay uncertain work.

## High-level `navigate`

```text
cdp.py [--websocket-url URL] navigate SESSION_ID URL
  (--format {outline,json} | --no-content)
  [--jq-context JQ_EXPRESSION]
  [--timeout SEC]
```

In content mode, `navigate` holds the session lock for the entire operation:
it clears the prior action baseline, calls `Page.navigate` with the supplied
URL, and reads `Page.getAIPageContent` with `includeDebugInfo:false` until
`.result.ready` is `true`. The first read is immediate; while `ready` is
`false`, another read occurs every 500 ms. The default 30-second `--timeout` is
one total budget for navigation and readiness. Only the ready full result
becomes the new action baseline.

`ready` is the browser's AI-page-content stability signal, not the DOM
document ready state. A document can already be `complete` while `ready`
remains false.

The output rules for `--format=outline`, `--format=json`, and
`--format=json --jq-context EXPRESSION` are exactly those of high-level
`content`. In particular, jq runs against `.result.content` and returns only
`{"contexts":[...],"fullJsonBytes":N}`; it does not add `ready`. Plain
JSON is the complete raw `Page.getAIPageContent` response and therefore
includes `ready`. Outline output is the page tree rendered as text.

If the total budget expires before a ready content result, the command returns
`error.type: navigate-timeout` with the requested URL,
`error.data.lastReady` (`boolean` or `null`), and available navigate metadata;
it does not expose the last not-ready content tree. A missing or non-boolean
`ready` is an invalid protocol response rather than an indefinitely loading
page.

`--no-content` is mutually exclusive with `--format` and `--jq-context`. It
only calls `Page.navigate`, returns that raw CDP response, and leaves the
session without an action baseline. A navigate response with non-empty
`errorText` or `isDownload:true` takes the same raw-response path even when a
content format was requested. A CDP/JSON-RPC error is returned unchanged.

## High-level `content`

```text
cdp.py [--websocket-url URL] content SESSION_ID
  --format {outline,json}
  [--jq-context JQ_EXPRESSION]
  [--timeout SEC]
```

`--format` is required. Every invocation fetches one fresh
`Page.getAIPageContent` result with `includeDebugInfo:false`; it does not poll
`ready` and never serves the session's stored baseline as content. A successful
read replaces the session's in-memory action baseline with the complete,
unfiltered result before formatting. The baseline is not persisted, and
outline output therefore does not discard any data used by later actions or
diffs.

When a new read is needed, use `--format=json --jq-context` first to locate a
known DOM target for a known action. Use `--format=outline` first to read
ordinary visible information or understand a page with no specific search
direction. Reuse a still-valid prior observation instead of reading again, and
use targeted jq when the task depends on hierarchy, repeated structures,
control state, or fields that outline omits.

Outline formatting performs a pre-order depth-first traversal beginning at
`.result.content`; a skipped node does not skip its descendants. For each node:

- Join its non-empty string `text`, `label`, and `placeholder` fields in that order with ` | `.
- If `id` is absent, append the joined element text followed by one newline. If both `id` and element text are absent, append nothing.
- If `id` is present, append `[ID|ACTION1,ACTION2]ELEMENT_TEXT` followed by one newline. Missing or empty `action` produces an empty action slot, such as `[7|]`.

No braces, indentation, quoting, final trimming, or extra newlines are added. Every emitted node contributes its specified trailing newline. Text is preserved as returned. A tree with no qualifying nodes produces empty stdout.

Use `--format=json` when complete structure or jq filtering is needed. Without `--jq-context`, this mode preserves the existing behavior: output is the same complete raw JSON returned by a direct `Page.getAIPageContent` call. With `--jq-context`:

- The jq expression runs directly against `.result.content`. The CLI does not wrap it in `path(...)`, interpret its output as paths, look up parents, or promote matches to ancestors.
- Every JSON value jq emits is appended verbatim to `contexts`, in emission order. Zero emissions produce `contexts: []`.
- Objects, arrays, strings, numbers, booleans, and null are all valid emissions. An emitted array remains one array-valued entry; entries are neither flattened nor de-duplicated.
- The command returns `{"contexts":[...],"fullJsonBytes":N}`. `fullJsonBytes` is the UTF-8 size of the complete unfiltered response in the CLI's compact form, excluding any trailing whitespace.

Every JSON success or error is one compact JSON value without a trailing newline. `--jq-context` requires `--format=json`; combining it with `--format=outline` is rejected before daemon access. Invalid jq syntax and jq evaluation failures return `error.type: jq-context`. If jq emits values before failing, those partial values are discarded. Context output is not additionally redacted or truncated. Before a jq read, combine all exact targets and evidence needed from that page state in one expression. If the result matches but is insufficient, broaden at most once from the target to its direct parent or smallest useful semantic container; fetch one full JSON response only when that still cannot answer the task. If a valid query returns `contexts: []` or no usable match because the page wording is uncertain, read one outline to discover the actual wording. Use an unambiguous actionable ID from that outline directly; rerun jq once only when the outline reveals wording but not enough structure. Treat `error.type: jq-context` as a query failure rather than a missing target.

The full page fetch and baseline update happen before jq filtering. A successful zero-emission query therefore still updates the baseline. If jq filtering fails, the command returns an error but the successfully fetched full result remains the session's latest action baseline.

When the same element appears in a later content response, use its newest ID. A partial context that omits an element does not by itself invalidate that element's last observed ID. Refresh content after navigation or substantial DOM reconstruction and do not use IDs known to be stale.

## High-level `action`

```text
cdp.py [--websocket-url URL] action SESSION_ID NODE_ID ACTION
  [--text TEXT]
  [--key KEY]
  [--value VALUE | --values [VALUE ...]]
  [--diff | --outline | --jq-context JQ_EXPRESSION]
  [--timeout SEC]
```

Establish a baseline first with a content-producing `navigate` or a `content`
read whose format matches the task, and prove the target's identity and
advertised action. `--diff`, `--outline`, and `--jq-context` are mutually
exclusive. `action` holds the session lock while it performs one
`Page.performDOMAction`, observes changes, and handles one of these outcomes:

- Same page: it always fetches and validates fresh full page content and replaces the baseline with the new result. With no observation flag it returns only `perform` and `changes`; `--diff` also returns `contentDiff`; `--outline` returns `outline` and `fullJsonBytes`; `--jq-context` returns `contexts` and `fullJsonBytes`.
- New target: if any observed effect contains `new_target` or `newTargetId`, it returns only the perform result and final changes. It does not read the original page, return an outline, diff, or jq result, or update the original session baseline. Attach the new target and read it with the format appropriate to the next task, using outline when its structure is unknown. Before later acting on the original session, refresh its old baseline with a task-appropriate content read.
- Error: a CDP error or an individual changes timeout is not retried. A perform result with `ok:false` fails as `error.type: action-result` and preserves its reason. Once both `perform` and `changes` have completed, a later content, validation, diff, or formatting failure includes those completed values in `error.data`.

`--value` and `--values` are mutually exclusive and are sent only when explicitly present. Use `select --value VALUE` for a single select. Use `select --values VALUE ...` for a multiple select's complete replacement set, or bare `select --values` to clear it. Empty strings are valid values. Read exact values from the select's `options` array instead of using option text.

With `--outline`, the response embeds the same pre-order outline produced by `content --format=outline` as an `outline` JSON string and reports `fullJsonBytes` for the complete unfiltered response. It formats the page content already fetched inside the action lock, so it does not issue a second `Page.getAIPageContent` request. Use it after navigation or broad DOM reconstruction when the next page structure is not known. If formatting fails after a successful perform step, the error preserves completed `perform` and `changes` values in `error.data`.

With `--diff`, `contentDiff` contains the smallest reliably aligned, non-overlapping roots whose complete subtrees describe content added or updated in the new `.content` tree, in new-tree document order. When a node's own fields change, that complete new node subtree is returned; when only descendants change, the diff descends to those nodes. New nodes and nodes moved to a new position are returned, while nodes that exist only in the old tree are omitted. When similar sibling nodes cannot be aligned reliably, the diff conservatively returns the affected new sibling roots so that it never drops new content. Consequently, an unchanged tree, a pure deletion, or a URL-only change returns `contentDiff: []`. Top-level result metadata such as `url` is outside the page-node tree; use the action changes effects for navigation details. `contentDiff` is not an RFC 6902 patch and cannot be applied to reconstruct the new result.

With `--jq-context`, the expression runs against the fresh post-action `.result.content` using exactly the same emission and aggregation rules as high-level `content --format=json --jq-context`: the response preserves `perform` and `changes`, replaces `contentDiff` with `contexts`, and reports `fullJsonBytes` for the complete unfiltered `Page.getAIPageContent` response. The full page is already fetched inside the action lock, so this mode does not issue a second content request and can select unchanged parents or siblings that would not appear in a diff. Jq syntax is checked before performing the action. A data-dependent evaluation failure after the action returns `error.type: jq-context`, discards partial jq output, and includes the completed `perform` and `changes` values in `error.data`.

The action `--timeout` is only the changes observation window, defaults to 3 seconds, and must be a finite value of at least 1 second. The first changes read occurs after 1 second. While a successful read contains only `no_op` effects, the command may read again every second until the deadline. This is best-effort polling over a one-shot, timing-sensitive API; later reads are not guaranteed to belong reliably to the action. A deadline is a normal ending: the response keeps only the final changes result, adds no `timedOut` or polling-history field, and the same-page branch still performs its requested observation.

`Page.performDOMAction.result.ok:true` means the operation ran; it does not prove the expected page state or business persistence. Verify the returned changes together with `--outline`, `--diff`, or a task-specific `--jq-context` query against the expected text, value, selection, target, navigation, or other observable result. Fetch relevant fresh content only when the selected observation is insufficient.

After any high-level `action` error, call `content --format=outline` or an appropriate `--format=json` read before issuing another action. A perform step may have succeeded before changes or content observation failed. Most such failures leave the previous baseline unchanged; a post-action outline or jq formatting failure occurs after the new full content has become the baseline, but the expected result still has not been verified.

## `Page.getAIPageContent`

Request:

```json
{"includeDebugInfo": false}
```

Always include that exact field and value in a raw call. The transport enforces it on every call path; high-level `content` supplies it automatically.

The result contains:

- `url`: the current page URL.
- `ready`: whether the current document is ready for AI page content
  consumers. It becomes true after the browser detects DOM stability or uses
  its network-idle fallback, remains latched for that document, and resets when
  the current document is replaced or reopened.
- `content`: the structured page tree. Common fields include `children`, `role`, `text`, `id`, and `action`; form controls may also expose values and constraints.
- `id`: an ephemeral interaction ID, present only on eligible nodes in non-debug content.
- `action`: an array of actions advertised for that node.
- Native select nodes expose `value` and an `options` array. Every option has `text`, `value`, `selected`, and `disabled`; `multiple` is present only when true. Options are data on the select, not child nodes.

Use a valid ID observed for the element and require its advertised action. When the same element is observed again, prefer its newest ID. Refresh content after navigation or DOM reconstruction. If a required node lacks an ID or action, use an appropriate standard CDP command or report the limitation; do not seek debug-only data.

Non-debug content can still contain sensitive form values, including password text. Keep responses out of logs and files, and expose only the minimum data needed for the task.

## `Page.performDOMAction`

Parameters:

- `id` (integer, required): an ID observed for the target element; use the newest ID when that element has been observed again.
- `action` (required): `click`, `input`, `input_submit`, `focus`, `change`, `mousemove`, `keydown`, or `select`.
- `text` (optional): text for input actions or key text for a keydown action.
- `key` (optional): key value for keydown; the protocol defaults it to Enter.
- `value` (optional): exact option value required by a single-select action.
- `values` (optional array of strings): complete option-value set required by a multiple-select action; an empty array clears the selection.

Result:

- `ok`: whether the action was performed.
- `reason`: explanation when `ok` is `false`.
- `targetLabel`: resolved human-readable target label when available.

`ok:true` confirms only that the operation was performed, not that an expected page or business state occurred. Check two independent failure forms:

1. A CDP/JSON-RPC error, such as an invalid enum or transport/session failure.
2. A successful protocol response whose result has `ok: false` and a `reason`, such as a missing node or required text.

The content generator can advertise `input` for contenteditable or autocomplete-backed elements while the action executor accepts input only for native input and textarea controls. Treat `ok: false` as authoritative; do not retry blindly.

A select action accepts only a native, enabled select. A single select chooses the first DOM option whose value matches and fails if that option is disabled. A multiple select selects every enabled matching option and replaces the previous set. Unknown or disabled requested values fail atomically; an unchanged requested selection succeeds without dispatching input or change events.

## `Page.getAIPageActionChanges`

The result contains an `effects` array for the most recent action in the same DevTools session. Effect kinds are:

- `navigation`
- `new_target`
- `same_page_mutation`
- `focus_changed`
- `value_changed`
- `download`
- `no_op`
- `failed`

Each effect includes the originating `action`, `targetNodeId`, `effect`, and `pageState`. Depending on the effect it can include labels, before/after URLs or values, new-target metadata, download metadata, or a reason. Page-state values are `settled`, `loading`, `blocked`, `timeout`, and `unknown`.

These rules apply to a raw `Page.getAIPageActionChanges` call:

- Collect effects once, after the expected event or a bounded settle. The read consumes renderer-side state, so polling can lose or misattribute results.
- Collect before performing another action. Starting a new action clears the previous pending observation.
- Avoid reading too early. Slow navigation, target creation, and download effects can arrive after the action response and be missed permanently by an early read.
- Treat the first effect response as the sole observation. A browser-side fast path can return before renderer effects are merged and can leave stale renderer state for a later read; a second read is not a reliable catch-up mechanism.
- Treat `no_op` as "no change observed at collection time," not proof that no delayed effect can occur.

When `newTargetId` is present, call `attach NEW_TARGET_ID`, use the returned
session ID, and read page content using the format appropriate to the next
task; use outline when the new page's structure is unknown. `attach` selects
the connection independently, so repeat the same selector: use
`--lexmount-session-id LEXMOUNT_SESSION_ID` for Lexmount or
`--websocket-url URL` for an explicit custom endpoint.

The high-level `action` command deliberately wraps this raw one-shot API in bounded best-effort polling. That does not make repeated raw reads reliable. Do not reproduce the polling manually; use `action --outline` for an unknown post-action page, `action --diff` for a small unknown mutation, or `action --jq-context` for a task-specific query over the complete new page.
