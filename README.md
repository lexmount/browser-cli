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
3. 不要把 API Key 输出到日志、README、提交记录或聊天回复里。

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
6. 引导我打开 https://browser.lexmount.cn 并登录账号。
7. 引导我在 browser.lexmount.cn 控制台中找到当前 Project ID，并创建或复制 API Key。
8. 引导我运行下面命令生成本机 shell export 模板，并只在本机终端里填入真实值：
   browser-cli auth export-env
   export LEXMOUNT_API_KEY="<从 browser.lexmount.cn 获取的 API Key>"
   export LEXMOUNT_PROJECT_ID="<从 browser.lexmount.cn 获取的 Project ID>"
9. 告诉我中国区默认会使用 https://api.lexmount.cn，通常不需要设置 LEXMOUNT_BASE_URL。
10. 如果我希望长期保存配置，引导我把这些 export 写入当前 shell 配置文件，例如 ~/.zshrc 或 ~/.bashrc。
11. 运行下面命令验证：
   browser-cli --help
   browser-cli doctor
12. 如果验证失败，请按顺序排查：
   - uv 是否可用
   - browser-cli 是否在 PATH 中
   - browser-cli auth status 是否显示 configured 为 true
   - browser-cli doctor 的 checks 中哪一项失败
   - LEXMOUNT_API_KEY 是否已设置
   - LEXMOUNT_PROJECT_ID 是否已设置
   - 如果设置了 LEXMOUNT_BASE_URL，它是否为正确的 API endpoint

完成后告诉我：
- browser-cli 的安装路径
- 验证命令是否通过
- 我还需要手动做什么
```

## Manual Install

```bash
uv tool install git+https://github.com/lexmount/browser-cli.git
browser-cli --help
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
browser-cli auth login
browser-cli auth login --project-id <project-id> --scope browser:actions --expires-in 24h
browser-cli auth export-env
browser-cli auth export-env --from-current
```

`auth export-env` prints placeholder shell commands by default. With
`--from-current`, it reuses current environment values but still masks
`LEXMOUNT_API_KEY` unless `--reveal-secrets` is explicitly passed in a trusted
local terminal.

After credentials are configured, run the self-check:

```bash
browser-cli doctor
```

Use `browser-cli doctor --skip-api` only when the live API should not be called.

## Commands

Authentication:

```bash
browser-cli auth status
browser-cli auth login
browser-cli auth login --project-id <project-id> --scope browser:sessions --scope browser:actions --expires-in 24h
browser-cli auth export-env
browser-cli auth export-env --from-current --include-base-url
```

`auth login` returns both the currently available `manual_env` flow and a
machine-readable `connect_from_codex` contract. The latter includes the planned
`https://browser.lexmount.cn/connect/codex` URL, optional `project_id`, repeated
`scope` query parameters, requested `expires_in`, expected outputs, and the
browser site requirements needed before device-code or scoped-token login can
be marked available.

Diagnostics:

```bash
browser-cli doctor
browser-cli doctor --skip-api
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
browser-cli action reload --session-id <session_id>
browser-cli action go-back --session-id <session_id>
browser-cli action go-forward --session-id <session_id>
browser-cli action wait-url --session-id <session_id> --url /dashboard
browser-cli action wait-load-state --session-id <session_id> --state complete
browser-cli action wait-network-idle --session-id <session_id> --idle-ms 500
browser-cli action get-text --session-id <session_id> --selector "main"
browser-cli action exists --session-id <session_id> --selector "button[type=submit]"
browser-cli action count --session-id <session_id> --selector ".item"
browser-cli action wait-count --session-id <session_id> --selector ".item" --count 3 --comparison gte
browser-cli action query --session-id <session_id> --selector ".item" --max-nodes 20
browser-cli action get-attribute --session-id <session_id> --selector "a" --name href
browser-cli action wait-attribute --session-id <session_id> --selector "button" --name aria-busy --state absent
browser-cli action wait-text --session-id <session_id> --text "Ready" --selector "main"
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
browser-cli action dispatch-event --session-id <session_id> --selector "input[name=q]" --event input --event change
browser-cli action submit --session-id <session_id> --selector "form"
browser-cli action scroll --session-id <session_id> --y 600
browser-cli action scroll --session-id <session_id> --selector ".pane" --y 300
browser-cli action scroll-into-view --session-id <session_id> --selector "button[type=submit]"
browser-cli action bounding-box --session-id <session_id> --selector "button[type=submit]"
browser-cli action select-option --session-id <session_id> --selector "select" --value pro
browser-cli action check --session-id <session_id> --selector "input[type=checkbox]"
browser-cli action uncheck --session-id <session_id> --selector "input[type=checkbox]"
browser-cli action hover --session-id <session_id> --selector ".menu"
browser-cli action press --session-id <session_id> --selector "input[name=q]" --key Enter
browser-cli action click-text --session-id <session_id> --text "Submit"
browser-cli action click-role --session-id <session_id> --role button --name "Submit"
browser-cli action fill-label --session-id <session_id> --label "Email" --text "me@example.com"
browser-cli action form-snapshot --session-id <session_id> --selector "form" --max-nodes 50
browser-cli action accessibility-snapshot --session-id <session_id> --max-nodes 100
browser-cli action interactive-snapshot --session-id <session_id>
```

`reload`, `go-back`, `go-forward`, `wait-url`, `wait-load-state`,
`wait-network-idle`, `get-text`, `exists`, `count`, `query`, `get-attribute`,
`wait-count`, `wait-attribute`, `wait-text`, `focus`, `get-value`,
`wait-value`, `blur`, `storage-get`, `storage-set`, `storage-remove`,
`storage-clear`, `wait-storage`, `cookie-get`, `cookie-set`, `cookie-delete`,
`cookie-clear`, `wait-cookie`, `clear`, `set-value`, `dispatch-event`,
`submit`, `scroll`, `scroll-into-view`, `bounding-box`, `select-option`,
`check`, `uncheck`, `hover`, `press`, `click-text`, `click-role`,
`fill-label`, `form-snapshot`, `accessibility-snapshot`, and
`interactive-snapshot` are implemented as eval-backed DOM actions while the
runtime action surface catches up. They are intended to reduce agent-written
JavaScript for common page work. For missing matches, parse structured fields
such as `found`, `exists`, `checked`, `selected`, `clicked`, `filled`,
`focused`, `value`, `readable`, `blurred`, `set`, `removed`, `cleared`,
`deleted`, `items`, `cleared_count`, `requested_count`, `state`,
`attribute_found`, `requested_value`, `network_idle`, `quiet_ms`, `submitted`,
`dispatched`, `dispatched_events`, `fields`, `value_masked`, `bounding_box`,
`in_viewport`, or `navigation_requested` from `result`.

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
browser-cli case validate --file case.yaml
browser-cli case run --file case.yaml
browser-cli doctor
browser-cli direct-url
browser-cli prepare
browser-cli list-contexts
browser-cli close-session --session-id <session_id>
```

## JSON Output

All command output is JSON.

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

`browser-cli doctor` returns a `checks` array with `pass`, `fail`, or `skipped`
statuses for install/version, environment, direct URL, and API connectivity
checks. It masks `api_key` in direct URLs and diagnostic error messages by
default. Failed or skipped checks may include a `fix` object with a stable
`code`, recommended `commands`, relevant `env` names, and concise `guidance`;
agents should prefer those fields when telling the user how to repair setup.

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
browser-cli action wait-load-state --session-id <session_id> --state complete
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
  `set-value`, or `clear` -> `wait-value` or `get-value` -> `blur` if validation is
  focus-driven -> `select-option` or `check` -> `dispatch-event` if explicit
  `input`/`change` is needed -> `submit --selector <form-or-field>`,
  `click-role --role button --name <text>` or `click-text` -> `wait-url` or
  `wait-text`.
- Visible button/link: `click-role`, then `click-text`, then `scroll-into-view`
  and selector `click` after `exists` or `bounding-box` confirms a stable
  selector.
- Navigation or async refresh: use `reload`, `go-back`, or `go-forward`, then
  confirm with `wait-url`, `wait-load-state`, `wait-network-idle`, `wait-text`,
  or `snapshot`.
- Menu or keyboard flow: `focus`, `hover`, `press`, or `dispatch-event`, then
  inspect again with `interactive-snapshot`.
- Read results: `wait-count` for dynamic lists, `wait-attribute` for DOM state
  changes, `get-text` for known selectors, or `snapshot` when the selector is
  unknown.
- Browser state: use `storage-get` to inspect local/session storage, `storage-set`
  to adjust feature flags or onboarding state, and `storage-remove` or
  `storage-clear --prefix <prefix>` for targeted cleanup. Use `wait-storage`
  after actions that should create/remove keys. Use `cookie-get`, `cookie-set`,
  `cookie-delete`, or `cookie-clear` for document.cookie-visible cookies, and
  `wait-cookie` after consent/login flows; HttpOnly cookies are not visible
  through this action surface.
- Debug candidate selectors: use `count` for cardinality, `query` for node
  metadata, `get-attribute` for href/value/aria checks, then `wait-count` or
  `wait-attribute` for async DOM changes.
- Final evidence: `screenshot`, then close the session unless it should stay
  open.

Use `session create --context-metadata-json '{"purpose":"codex-login"}'
--create-context-if-missing --context-mode read_write` when login state or
cookies should survive between sessions. The command picks the first reusable
matching context, creates one if requested, then returns `context_reuse` with
candidate contexts, `created`, `selected`, and locked/reusable details. Use
`context pick --metadata-json '{"purpose":"codex-login"}'` when you need to
inspect or report candidates before creating a session.

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
4. Add a "Verify CLI" section that tells users to run `browser-cli doctor`
   after setting env vars and shows how to interpret failed `checks`.
5. Show the selected `Project ID`, scoped credential status, copyable
   `browser-cli auth export-env` / `export ...` commands, and revoke/expiration
   details for the issued credential.
6. Show `browser-cli auth login`, `auth status`, and `auth export-env` as the
   local setup path until device-code is available.
7. Longer term, support device-code or OAuth-style authorization so Codex can
   ask the user to approve access in the browser and then receive a local,
   short-lived token without the user manually copying API keys.
