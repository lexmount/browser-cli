# browser-cli

`browser-cli` is a command-line interface for operating Lexmount remote browser
sessions from local shells, Codex, and other agents.

It owns command parsing, JSON output, installation docs, and agent-facing
ergonomics. Browser lifecycle and page action behavior stay in
`lex-browser-runtime`, so this project does not maintain a second copy of the
runtime implementation.

## Codex Install Prompt

Copy this prompt into Codex when you want Codex to install and configure the
CLI for you:

```text
请帮我安装并配置 Lexmount browser-cli，用于在 Codex 中操作 Lexmount 远程浏览器。

约束：
1. 不要让我把 API Key 或 Project ID 粘贴到聊天里。
2. 只指导我在本机 shell 中设置环境变量，或写入本机 shell 配置文件。
3. 不要在聊天回复、日志、README、测试、提交记录或 PR 描述里输出 API Key。
4. 如果命令输出中出现 masked/revealed/contains_secrets/usable 字段，请按这些字段判断是否可以复制到聊天里；revealed secret 永远不要复制到聊天。
5. 不要复述任何 secret 的真实值。

步骤：
1. 检查本机是否已经安装 uv：
   uv --version
2. 如果没有 uv，提示我先安装 uv：
   curl -LsSf https://astral.sh/uv/install.sh | sh
3. 安装 browser-cli：
   uv tool install git+https://github.com/lexmount/browser-cli.git
4. 运行下面命令查看本机是否已经配置凭证：
   browser-cli auth status
5. 如果未配置，引导我运行：
   browser-cli auth login
6. 如果我希望直接打开本机浏览器，可以让我运行：
   browser-cli auth login --open
7. 从 auth login 的 JSON 中读取 connect_from_codex.url 或 handoff.login_url，优先引导我打开 https://browser.lexmount.cn/connect/codex，并登录账号。
8. 引导我在 browser.lexmount.cn 控制台中选择正确项目，确认当前 Project ID，并创建或复制面向 agent 的 scoped API Key。
9. 引导我运行下面命令生成本机 shell export 模板，并只在本机终端里填入真实值：
   browser-cli auth export-env
   export LEXMOUNT_API_KEY="<从 browser.lexmount.cn 获取的 API Key>"
   export LEXMOUNT_PROJECT_ID="<从 browser.lexmount.cn 获取的 Project ID>"
10. 只有在本机可信 shell 中需要可直接执行的 export 行时，才让我自己运行：
   browser-cli auth export-env --from-current --reveal-secrets
   browser-cli auth export-env --reveal-secrets
   并提醒我不要把该输出粘贴到聊天里。
11. 告诉我中国区默认会使用 https://api.lexmount.cn，通常不需要设置 LEXMOUNT_BASE_URL。
12. 如果我希望长期保存配置，引导我把这些 export 写入当前 shell 配置文件，例如 ~/.zshrc 或 ~/.bashrc。
13. 运行下面命令验证：
   browser-cli --help
   browser-cli doctor --json
   browser-cli doctor --smoke-session
   browser-cli session list
   其中 doctor 成功判据是 ok=true、failed=0、ready_for_browser_actions=true；如果运行了 smoke-session，browser_smoke_session.status 应该是 pass，且 created=true、closed=true。
14. 如果验证失败，请按顺序排查：
   - uv 是否可用
   - browser-cli 是否在 PATH 中
   - browser-cli auth status 是否显示 configured 为 true
   - browser-cli doctor 的 checks 中哪一项 fail 或 warn
   - browser-cli doctor --smoke-session 的 browser_smoke_session 是否创建或关闭失败；如果 created=true 且 closed=false，按 fix.commands 手动关闭临时 session
   - LEXMOUNT_API_KEY 是否已设置
   - LEXMOUNT_PROJECT_ID 是否已设置
   - 如果设置了 LEXMOUNT_BASE_URL，它是否为正确的 API endpoint
   - browser.lexmount.cn 中选择的 Project 是否和 LEXMOUNT_PROJECT_ID 一致
   - API Key 是否已过期、被 revoke，或缺少 browser session/context/action 权限

完成后告诉我：
- browser-cli 的安装路径
- 验证命令是否通过
- 我还需要手动做什么
- 不要复述任何 secret 的真实值
```

## Manual Install

```bash
uv tool install git+https://github.com/lexmount/browser-cli.git
browser-cli --help
browser-cli commands --names-only
```

For local development:

```bash
uv sync --all-groups
uv run browser-cli --help
uv run pytest tests -q
uv run ruff format --check .
uv run ruff check .
```

## Credentials

`browser-cli` reads the same environment variables as `lex-browser-runtime`:

```bash
export LEXMOUNT_API_KEY="<api-key>"
export LEXMOUNT_PROJECT_ID="<project-id>"
```

Optional:

```bash
export LEXMOUNT_BASE_URL="https://api.lexmount.cn"
export LEXMOUNT_REGION="<region>"
```

Treat API keys as secrets. The CLI masks `api_key` in generated direct browser
URLs unless you pass an explicit reveal flag.

Use these local auth helpers:

```bash
browser-cli auth status
browser-cli auth status --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth token-info --required-scope browser:actions
browser-cli auth refresh --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth logout --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth login
browser-cli auth login --open
browser-cli auth login --device-code
browser-cli auth login --project-id <project-id> --scope browser:actions --expires-in 24h
browser-cli auth export-env
browser-cli auth export-env --from-current
```

`auth export-env` prints placeholder shell commands by default. With
`--from-current`, it reuses current environment values but still masks
`LEXMOUNT_API_KEY` unless `--reveal-secrets` is explicitly passed in a trusted
local terminal.
`auth status` and `auth token-info` report local device-token metadata from
`~/.config/lexmount/browser-cli/credentials.json`,
`LEXMOUNT_BROWSER_CREDENTIALS_FILE`, or `--credentials-file` without printing
access or refresh token values. Until bearer-token runtime support lands,
`runtime_auth_usable` is true only when env API-key credentials are configured.
Use `auth token-info --required-scope <scope>` to check scoped-token coverage.
Use `auth refresh --credentials-file <path>` to inspect whether local
device-token metadata needs refresh. It currently reports `refresh_available:
false` and `refreshed: false` until browser.lexmount.cn exposes the refresh
endpoint; it never prints access or refresh token values.
Use `auth logout --credentials-file <path>` to remove local device-token
metadata without changing environment variables. `auth logout --revoke`
currently reports `revoke_available: false` and reminds you to revoke from
browser.lexmount.cn until remote revoke is implemented.

After credentials are configured, run the self-check:

```bash
browser-cli doctor
browser-cli doctor --json
browser-cli doctor --smoke-session
```

`browser-cli` output is always JSON; `--json` is accepted as an agent
compatibility no-op at the top level and after subcommands. Use
`browser-cli doctor --skip-api` only when the live API should not be called.
Use `browser-cli doctor --smoke-session` when you need stronger proof that the
credentials can create and close a temporary browser session, not just reach the
API.

For machine-readable command discovery, run:

```bash
browser-cli commands
browser-cli commands --names-only
browser-cli commands --group action
```

`commands` returns the current parser-backed command catalog, option metadata,
browser target requirements, JSON/secret policies, and agent entrypoint recipes.
Agents should use it when deciding whether a first-class action exists before
writing custom JavaScript.

## Commands

Authentication:

```bash
browser-cli auth status
browser-cli auth status --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth token-info --required-scope browser:sessions --required-scope browser:actions
browser-cli auth refresh --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth logout --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth login
browser-cli auth login --open
browser-cli auth login --device-code
browser-cli auth login --project-id <project-id> --scope browser:sessions --scope browser:actions --expires-in 24h
browser-cli auth export-env
browser-cli auth export-env --from-current --include-base-url
```

`auth refresh` reports local refresh state, including `refresh_needed`,
`has_refresh_token`, `refresh_available`, `refreshed`, and `reason`. Remote
refresh is not implemented yet, so agents should use its `next_steps` and keep
using env API-key credentials for browser actions today.

`auth login` returns both the currently available `manual_env` flow and a
machine-readable `handoff` plus `connect_from_codex` contract. `handoff`
includes the Connect URL, copyable local commands, required env vars, safe
secret-handling rules, and doctor verification command. `connect_from_codex`
includes the planned `https://browser.lexmount.cn/connect/codex` URL, optional
`project_id`, repeated `scope` query parameters, requested `expires_in`,
expected outputs, structured `setup_blocks`, `requested_scope_details`,
`site_capabilities`/`site_capability_status`, and the browser site requirements
needed before device-code or scoped-token login can be marked available.
`setup_blocks` groups install, Connect, local env, and verification commands
with secret placeholder and chat-safety metadata so browser.lexmount.cn can
render copy buttons without guessing which commands are local-shell-only.
`requested_scope_details` gives browser.lexmount.cn labels, descriptions,
permission names, risk levels, and destructive markers for known scopes, while
unknown future scopes are marked with `known: false`. Capability ids currently include
`project_id_display`, `scoped_api_key`, `copy_install_and_env`,
`doctor_verification`, `scoped_key_lifecycle`, and `device_code_oauth`.
Use `auth login --open` when you want the CLI to open the Connect URL in the
default browser; JSON output still includes `open_result` so agents can continue
or fall back to copying the URL when the browser cannot be opened.
`auth login --device-code` reserves the future device-code command surface and
currently returns `flow: "device_code"`, `available: false`,
`reason: "browser_site_endpoint_missing"`, required browser/API endpoints, and a
`fallback_handoff` for the manual env flow.

Diagnostics:

```bash
browser-cli commands
browser-cli commands --names-only
browser-cli commands --group action
browser-cli doctor
browser-cli doctor --json
browser-cli doctor --smoke-session
browser-cli doctor --skip-api
browser-cli doctor --credentials-file ~/.config/lexmount/browser-cli/credentials.json
```

Session management:

```bash
browser-cli session create
browser-cli session create --create-context
browser-cli session create --context-id <context_id> --context-mode read_write
browser-cli session create --context-metadata-json '{"purpose":"codex-login"}' --create-context-if-missing
browser-cli session list --status active
browser-cli session get --session-id <session_id>
browser-cli session close --session-id <session_id>
browser-cli session keepalive --session-id <session_id> --duration 60
```

Context management:

```bash
browser-cli context create
browser-cli context create --metadata-json '{"purpose":"codex"}'
browser-cli context list --limit 20
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
browser-cli action click --session-id <session_id> --selector "button[type=submit]"
browser-cli action type --session-id <session_id> --selector "input[name=q]" --text "hello"
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
browser-cli action exists --session-id <session_id> --selector "button[type=submit]"
browser-cli action count --session-id <session_id> --selector ".item"
browser-cli action wait-count --session-id <session_id> --selector ".item" --count 3 --comparison gte
browser-cli action wait-state --session-id <session_id> --selector "button[type=submit]" --state enabled
browser-cli action query --session-id <session_id> --selector ".item" --max-nodes 20
browser-cli action get-attribute --session-id <session_id> --selector "a" --name href
browser-cli action wait-attribute --session-id <session_id> --selector "button" --name aria-busy --state absent
browser-cli action wait-text --session-id <session_id> --text "Ready" --selector "main"
browser-cli action wait-text --session-id <session_id> --text "Loading" --state absent
browser-cli action wait-role --session-id <session_id> --role button --name "Submit"
browser-cli action focus --session-id <session_id> --selector "input[name=q]"
browser-cli action get-value --session-id <session_id> --selector "input[name=q]"
browser-cli action wait-value --session-id <session_id> --selector "input[name=q]" --value "hello"
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
browser-cli action set-value --session-id <session_id> --selector "input[name=q]" --value "hello"
browser-cli action set-file-input --session-id <session_id> --selector "input[type=file]" --file ./avatar.png
browser-cli action dispatch-event --session-id <session_id> --selector "input[name=q]" --event input --event change
browser-cli action submit --session-id <session_id> --selector "form"
browser-cli action scroll --session-id <session_id> --y 600
browser-cli action scroll --session-id <session_id> --selector ".pane" --y 300
browser-cli action scroll-into-view --session-id <session_id> --selector "button[type=submit]"
browser-cli action bounding-box --session-id <session_id> --selector "button[type=submit]"
browser-cli action inspect --session-id <session_id> --selector "button[type=submit]"
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
browser-cli action interactive-only-snapshot --session-id <session_id>
```

`page-info`, `reload`, `go-back`, `go-forward`, `wait-url`, `wait-title`,
`wait-load-state`, `wait-network-idle`, `get-text`, `exists`, `count`, `query`,
`get-attribute`, `wait-count`, `wait-state`, `wait-attribute`, `wait-text`, `wait-role`, `focus`,
`get-value`, `wait-value`, `blur`, `storage-get`, `storage-set`, `storage-remove`,
`storage-clear`, `wait-storage`, `cookie-get`, `cookie-set`, `cookie-delete`,
`cookie-clear`, `wait-cookie`, `clear`, `set-value`, `set-file-input`,
`dispatch-event`, `submit`, `scroll`, `scroll-into-view`, `bounding-box`, `inspect`,
`select-option`, `select-label`, `check`, `uncheck`, `check-label`,
`uncheck-label`, `hover`, `press`, `press-key`, `click-text`, `click-role`,
`click-index`, `fill-label`,
`link-snapshot`, `table-snapshot`, `list-snapshot`, `text-snapshot`, `dialog-snapshot`, `wait-dialog`, `frame-snapshot`, `wait-frame`, `performance-snapshot`, `network-snapshot`, `wait-network`, `console-snapshot`, `wait-console`, `outline-snapshot`, `form-snapshot`, `accessibility-snapshot`,
`interactive-snapshot`, and its `interactive-only-snapshot` alias are implemented as eval-backed DOM actions while the
runtime action surface catches up. They are intended to reduce agent-written
JavaScript for common page work. For missing matches, parse structured fields
such as `found`, `exists`, `checked`, `selected`, `clicked`, `filled`,
`focused`, `value`, `readable`, `blurred`, `set`, `removed`, `cleared`,
`deleted`, `items`, `cleared_count`, `requested_count`, `state`,
`matched`, `state_values`, `attribute_found`, `requested_value`, `network_idle`,
`quiet_ms`, `submitted`, `dispatched`, `dispatched_events`, `fields`,
`value_masked`, `file_input`, `file_count`, `requested_files`, `bounding_box`,
`in_viewport`, `index`, `attributes`, `html_truncated`, `requested_option_label`,
`option_found`, `option_label`, `requested_checked`, `previous_checked`,
`changed`, `links`, `link_count`, `href`, `href_masked`, `absolute_url`,
`absolute_url_masked`, `same_origin`, `external`, `download`,
`tables`, `table_count`, `headers`, `rows`, `cells`, `row_count`, `cell_count`,
`lists`, `list_count`, `items`, `item_count`, `selected`, `checked`, `expanded`,
`texts`, `text_count`, `text_length`, `text_truncated`, `aria_live`,
`dialogs`, `dialog_count`, `total_dialog_count`, `requested_text`, `modal_only`,
`controls`, `control_count`, `controls_truncated`, `modal`,
`frames`, `frame_count`, `total_frame_count`, `src`, `src_masked`,
`frame_url`, `frame_url_masked`, `readable`, `readable_only`,
`same_origin_only`, `text_match`, `read_error`,
`navigation`, `resources`, `resource_count`, `initiator_type`,
`initiator_types`, `duration`, `transfer_size`, `response_status`,
`entries`, `entry_count`, `matched_count`, `buffered_count`, `source`, `level`,
`method`, `requested_method`, `status`, `ok`, `failed`, `failed_only`,
`request_has_body`, `duration_ms`, `text_masked`, `filename_masked`,
`url_masked`, `timed_out`, `requested_url`, `url_match`, `requested_source`,
`requested_status`, `requested_level`, `after_index`,
`headings`, `landmarks`, `outline_count`, `heading_count`, `landmark_count`,
`node_type`, `level`,
`total_candidate_count`, `ready_state`, `visibility_state`,
`viewport`, `scroll`, `body_text_length`, `html_length`, `language`,
`referrer`, `requested_title`, `case_sensitive`, `code`, `target`,
`target_info`, `modifiers`, `events`, `keydown_accepted`, or
`navigation_requested` from `result`.
For DOM/form actions, values from fields that look like password, token,
credential, secret, authorization, or API-key controls are masked by default.
When `value`, `previous_value`, `requested_value`, or `text` is `***`, inspect
`value_masked`, `previous_value_masked`, `requested_value_masked`,
`text_masked`, and related `*_length` fields before deciding whether the page
state is correct.
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

Each action must receive exactly one browser target:

```bash
--session-id <session_id>
--connect-url <cdp_websocket_url>
--direct-url
```

By default, action output masks `api_key` inside resolved direct connect URLs.
Use `--reveal-connect-url` only for local debugging.

Diagnostics, case files, and compatibility aliases:

```bash
browser-cli auth status
browser-cli auth export-env
browser-cli commands
browser-cli case validate --file case.yaml
browser-cli case run --file case.yaml
browser-cli doctor
browser-cli doctor --smoke-session
browser-cli direct-url
browser-cli prepare
browser-cli list-contexts
browser-cli close-session --session-id <session_id>
```

## JSON Output

All command output is JSON. `--json` is accepted as a no-op compatibility flag
for agents that habitually request machine-readable output; it can appear before
the command group or after subcommands, for example `browser-cli --json auth
status`, `browser-cli auth status --json`, or `browser-cli action snapshot
--session-id <session_id> --json`.

Successful commands include:

```json
{
  "ok": true,
  "command": "session.create"
}
```

Failed commands include:

```json
{
  "ok": false,
  "command": "session.create",
  "error": "configuration_error",
  "message": "..."
}
```

Agents should parse `ok`, `command`, and `error` first, then use
command-specific fields. Failure messages and payload fields are sanitized before
printing: `api_key`, token-like query parameters, and the current
`LEXMOUNT_API_KEY` value are masked unless a success command explicitly uses a
local reveal flag.

`browser-cli commands` returns a parser-backed command catalog with
`schema_version`, `groups`, `command_count`, `commands`, `json_output`,
`secret_policy`, and `agent_entrypoints`. Use `--names-only` for compact command
discovery and `--group action` when choosing a browser action. Action catalog
entries include `browser_target.exactly_one_of` so agents can supply exactly one
of `--session-id`, `--connect-url`, or `--direct-url`.

Argument parsing errors also return JSON on stdout with exit code `2`:

```json
{
  "ok": false,
  "command": "action.open-url",
  "error": "argument_error",
  "message": "the following arguments are required: --url",
  "usage": "usage: browser-cli action open-url ..."
}
```

Agents should use the `usage` field to repair malformed commands instead of
parsing stderr.

`browser-cli doctor` returns top-level `ok`, `failed`, `warnings`, `checked`,
`ready_for_browser_actions`, check-name arrays, and a `repair_plan` that
aggregates fix commands/env/guidance. Its `checks` array uses `pass`, `warn`,
`fail`, or `skipped` statuses for Python/runtime, install path, version,
command catalog, environment, direct URL, API connectivity, and optional browser
smoke-session checks. The `command_catalog` check verifies the installed CLI has
the commands expected by the Codex Skill and reports
`missing_required_commands` with upgrade guidance when the action surface is too
old. That required surface includes selector actions, press/hover/scroll,
get-text/exists, select/check/uncheck, role/text/label actions, accessibility
snapshot, and interactive-only snapshot. It masks `api_key` in direct URLs and
diagnostic error messages by default.
`doctor --smoke-session` creates and closes a temporary session after API
connectivity passes, then reports the `browser_smoke_session` check with
`created`, `closed`, `session_id`, and actionable close guidance if cleanup
fails. Failed, warning, or skipped checks may include a `fix` object with a
stable `code`, recommended `commands`, relevant `env` names, and concise
`guidance`; agents should prefer `repair_plan` when telling the user how to
repair setup. `doctor --json` is a no-op compatibility form because JSON is
already the only output format.

`browser-cli auth status` reports local credential presence without revealing
the API key. `browser-cli auth export-env` returns `commands` and `script`
fields; generated commands are placeholders or masked unless
`--from-current --reveal-secrets` is explicitly used locally.

## Suggested Agent Workflow

For a new browser task, agents should prefer this sequence:

```bash
browser-cli session create
browser-cli action open-url --session-id <session_id> --url <url>
browser-cli action wait-url --session-id <session_id> --url <url-or-fragment>
browser-cli action wait-title --session-id <session_id> --title <title-or-fragment>
browser-cli action wait-load-state --session-id <session_id> --state complete
browser-cli action page-info --session-id <session_id>
browser-cli action snapshot --session-id <session_id>
browser-cli action exists --session-id <session_id> --selector <selector>
browser-cli action click --session-id <session_id> --selector <selector>
browser-cli action wait-network-idle --session-id <session_id> --idle-ms 500
browser-cli action wait-text --session-id <session_id> --text <text>
browser-cli action wait-count --session-id <session_id> --selector <selector> --count <n> --comparison gte
browser-cli action wait-attribute --session-id <session_id> --selector <selector> --name <name>
browser-cli action type --session-id <session_id> --selector <selector> --text <text>
browser-cli action get-text --session-id <session_id> --selector <selector>
browser-cli action get-value --session-id <session_id> --selector <selector>
browser-cli action storage-get --session-id <session_id> --area local --key <key>
browser-cli action wait-storage --session-id <session_id> --area local --key <key>
browser-cli action cookie-get --session-id <session_id> --name <name>
browser-cli action wait-cookie --session-id <session_id> --name <name>
browser-cli action query --session-id <session_id> --selector <selector>
browser-cli action screenshot --session-id <session_id> --output /tmp/final.png
browser-cli session close --session-id <session_id>
```

Common agent recipes:

- Form submit: `interactive-snapshot` or `form-snapshot` -> `fill-label`,
  `set-value`, `set-file-input`, or `clear` -> `wait-value` or `get-value` ->
  `blur` if validation is focus-driven -> `select-label`, `select-option`,
  `check-label`, or `check` -> `wait-state --state enabled` or `wait-role` for
  async submit buttons -> `dispatch-event` if explicit `input`/`change` is needed ->
  `submit --selector <form-or-field>`,
  `click-role --role button --name <text>` or `click-text` -> `wait-url` or
  `wait-text`.
- Visible button/link: `wait-role` when the control appears asynchronously,
  then `click-role`, then `click-text`; run `link-snapshot` when the task is to
  choose, inspect, or report navigation URLs, then use `scroll-into-view` and
  selector `click` after `exists`, `inspect`, or `bounding-box` confirms a
  stable selector.
- Repeated list item: `list-snapshot` for menus, search results, listboxes, and
  task lists -> read `items`, `links`, `checked`, `selected`, and `expanded`;
  use `--selector` and `--max-items` to keep output bounded. Fall back to
  `query` -> choose a zero-based candidate -> `click-index` when the list is not
  semantic.
- Table or report data: `table-snapshot` -> read `headers`, `rows`, and `cells`;
  use `--selector`, `--max-rows`, and `--max-cells` to keep output bounded.
- Text, alerts, or status messages: `text-snapshot` -> read `texts`,
  `kind`, `aria_live`, `text_length`, and `text_truncated`; use `--selector`,
  `--max-nodes`, and `--max-chars` before falling back to full `snapshot`.
- Modal or blocking prompt: use `wait-dialog --text <text> --modal-only` when
  the prompt appears asynchronously, otherwise `dialog-snapshot`; read
  `dialogs`, `title`, `description`, `text`, `controls`, `control_count`, and
  link masks; then use `click-role`, `click-text`, or `click-index` for the
  chosen control.
- Embedded frame: `frame-snapshot` -> read `frames`, `src`, `readable`,
  `frame_url`, `body_text`, `read_error`, and `bounding_box`; same-origin
  frames can expose bounded text, while cross-origin frames usually require
  using the frame's URL or reporting that direct DOM inspection is unavailable.
- Page loading or slow resource diagnosis: `performance-snapshot` -> read
  `navigation`, `resources`, `initiator_types`, `duration`, `transfer_size`,
  and `response_status`; use `--initiator-type` and `--min-duration-ms` to keep
  output focused.
- Fetch/XHR diagnosis: run `network-snapshot --install-only` before the action,
  trigger the page behavior, then run `network-snapshot` to read masked request
  URLs, `method`, `status`, `ok`, `failed`, `duration_ms`, and
  `request_has_body`; use `--source`, `--method`, or `--failed-only` to narrow
  entries and `--clear` after collecting. To wait for a future request, run
  `wait-network --url <path> --method <method>`; add `--status`,
  `--failed-only`, or `--after-index` when stale buffered entries should be
  ignored.
- Console or page error diagnosis: run `console-snapshot --install-only` before
  the suspicious action, trigger the page behavior, then run `console-snapshot`
  to read `entries`, `source`, `level`, `method`, and masked `text`. Use
  `--clear` after collecting entries. To wait for a future error, run
  `wait-console --source pageerror --level error`; pass `--after-index` from a
  prior `console-snapshot` entry when stale buffered entries should be ignored.
- Page structure: `outline-snapshot` -> read `headings` and `landmarks` before
  deciding where to inspect, click, or scroll.
- Stuck selector: `inspect` to check `state.disabled`, `state.readonly`,
  `visible`, `in_viewport`, `attributes`, masked `value`, and optional sanitized
  HTML before trying another action.
- Navigation or async refresh: use `reload`, `go-back`, or `go-forward`, then
  confirm with `page-info`, `wait-url`, `wait-title`, `wait-load-state`,
  `wait-network-idle`, `performance-snapshot`, `wait-text`, or `snapshot`.
- Runtime errors: install `console-snapshot --install-only`, trigger the
  suspected action, read `console-snapshot` or wait with `wait-console`, then use
  `text-snapshot`, `wait-dialog`, `dialog-snapshot`, `wait-frame`, or `inspect`
  to correlate visible state with JS errors.
- Menu or keyboard flow: `focus`, `hover`, selector-scoped `press`,
  active/global `press-key`, or `dispatch-event`, then inspect again with
  `interactive-snapshot`.
- Dialog flow: use `wait-dialog` when the dialog appears asynchronously,
  otherwise `dialog-snapshot` for modal dialogs, alert dialogs, cookie banners,
  and confirmation prompts; choose a control from `controls`, then click
  semantically and confirm with `wait-text`, `wait-role`, or `text-snapshot`.
- Frame flow: use `wait-frame` when the iframe or embedded app appears
  asynchronously, otherwise `frame-snapshot` before writing frame-related
  JavaScript; use `readable`, `same_origin`, `frame_url`, and `read_error` to
  decide whether the agent can inspect the embedded page or needs a different
  browser workflow.
- Read results: `page-info` for URL/title/readyState/viewport checks,
  `wait-title` for async title changes, `wait-count` for dynamic lists,
  `list-snapshot` for menu/listbox/search-result/task-list content,
  `text-snapshot` for visible paragraphs, alerts, status messages, and bounded
  readable page text,
  `wait-attribute` for DOM attributes, `wait-state` for
  enabled/visible/checked/focused states, `get-text` for known selectors, or
  `snapshot` when the selector is unknown. Use `wait-text --state absent` when
  loading, toast, or error text should disappear.
- Browser state: use `storage-get` to inspect local/session storage, `storage-set`
  to adjust feature flags or onboarding state, and `storage-remove` or
  `storage-clear --prefix <prefix>` for targeted cleanup. Use `wait-storage`
  after actions that should create/remove keys. Use `cookie-get`, `cookie-set`,
  `cookie-delete`, or `cookie-clear` for document.cookie-visible cookies, and
  `wait-cookie` after consent/login flows; HttpOnly cookies are not visible
  through this action surface.
- Debug candidate selectors: use `count` for cardinality, `query` for node
  metadata, `inspect` for state, `get-attribute` for href/value/aria checks,
  then `wait-count`, `wait-state`, or `wait-attribute` for async DOM changes.
- Final evidence: `screenshot`, then close the session unless it should stay
  open.

Use `session create --context-metadata-json '{"purpose":"codex-login"}'
--create-context-if-missing --context-mode read_write` when login state or
cookies should survive between sessions. The command picks the first reusable
matching context, creates one if requested, then returns `context_reuse` with
candidate contexts, `created`, `selected`, `normalized_status`, `availability`,
`selection_summary`, and locked/reusable details. Treat
`availability: "available"` as reusable, `availability: "locked"` as busy, and
`availability: "unavailable"` as a state that needs a different context. Use
`context pick --metadata-json '{"purpose":"codex-login"}' --dry-run` when you
need to inspect or report candidates before creating a session; read
`selection_summary.recommended_next_action`, `decision_reason`,
`locked_matches`, `metadata_mismatches`, `reusable_matches`, and `would_create`
before deciding whether to reuse, create, wait, or adjust filters.

## Codex Skill

This repository includes a starter [`SKILL.md`](./SKILL.md) so the project can
evolve into a Codex skill. The skill stays a thin wrapper around this CLI:

- `SKILL.md` should teach agents when to use browser sessions, contexts, and
  actions.
- The skill should install or verify `browser-cli`.
- The skill should never store API keys in the skill directory.
- The skill should keep using JSON command output instead of importing Python
  internals directly.

## Suggestions For browser.lexmount.cn

The smoothest onboarding path would be a dedicated "Connect from Codex" flow:

1. Add `/connect/codex` and accept optional `project_id`, repeated `scope`, and
   `expires_in` query parameters generated by `browser-cli auth login`.
2. Add a scoped API key wizard for agent use, with clear permissions, optional
   expiration, and one-click revoke.
3. Provide a copyable install block:
   `uv tool install git+https://github.com/lexmount/browser-cli.git`.
4. Add a "Verify CLI" section that tells users to run
   `browser-cli doctor --json` and `browser-cli doctor --smoke-session` after
   setting env vars, then explains `ready_for_browser_actions` and
   `browser_smoke_session`.
5. Show the selected `Project ID`, scoped credential status, copyable
   `browser-cli auth export-env` / `export ...` commands, and revoke/expiration
   details for the issued credential.
6. Show `browser-cli auth login`, `auth status`, and `auth export-env` as the
   local setup path until device-code is available.
7. Longer term, support device-code or OAuth-style authorization so Codex can
   ask the user to approve access in the browser and then receive a local,
   short-lived token without the user manually copying API keys.
