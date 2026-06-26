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
4. 引导我打开 https://browser.lexmount.cn 并登录账号。
5. 引导我在 browser.lexmount.cn 控制台中找到当前 Project ID，并创建或复制 API Key。
6. 引导我在本机 shell 中设置：
   export LEXMOUNT_API_KEY="<从 browser.lexmount.cn 获取的 API Key>"
   export LEXMOUNT_PROJECT_ID="<从 browser.lexmount.cn 获取的 Project ID>"
7. 告诉我中国区默认会使用 https://api.lexmount.cn，通常不需要设置 LEXMOUNT_BASE_URL。
8. 如果我希望长期保存配置，引导我把这些 export 写入当前 shell 配置文件，例如 ~/.zshrc 或 ~/.bashrc。
9. 运行下面命令验证：
   browser-cli --help
   browser-cli direct-url
   browser-cli session list
10. 如果验证失败，请按顺序排查：
   - uv 是否可用
   - browser-cli 是否在 PATH 中
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

## Commands

Session management:

```bash
browser-cli session create
browser-cli session create --create-context
browser-cli session create --context-id <context_id> --context-mode read_write
browser-cli session create --resolve-context --metadata-match-json '{"site":"example.com","purpose":"login"}'
browser-cli session create --resolve-context --create-context --metadata-match-json '{"site":"example.com","purpose":"login"}'
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
browser-cli context resolve
browser-cli context resolve --create-if-missing
browser-cli context resolve --metadata-match-json '{"site":"example.com","purpose":"login"}'
browser-cli context resolve --context-id <context_id>
browser-cli context delete --context-id <context_id>
```

Persistent login context workflow:

```bash
browser-cli session create --resolve-context --create-context --metadata-match-json '{"site":"example.com","purpose":"login"}'
```

`session create --resolve-context` first resolves an `available` context and only
then starts the session. When used with `--create-context`, it creates a
matching context if none is available. If the matching context is `locked`,
metadata does not match, or no matching context exists without
`--create-context`, the command returns `ok:false` with `error:
context_not_reusable` and a `context_resolution.decision` object. This is the
safest one-command path for agents handling persistent login state.

`context resolve` only selects contexts whose status is `available`. If a
context is `locked`, another active session is using it; close that session or
create a new context before starting a read/write login-state session. Context
JSON includes a `reuse` object with `can_reuse_now`, `reason`, `next_steps`, and
`recommended_session_command`. `context resolve` also returns a top-level
`decision` object with `action`, `reason`, `can_start_session`,
`should_create_context`, `should_close_session`, and `selected_context_id` so
agents can branch without inferring from free-form text. Use
`--metadata-match-json` to reuse only contexts whose metadata contains the
requested keys and values; if `--create-if-missing` is used without
`--metadata-json`, the match metadata is used for the newly created context.

Browser actions:

```bash
browser-cli action open-url --session-id <session_id> --url https://example.com
browser-cli action wait-selector --session-id <session_id> --selector "main"
browser-cli action click --session-id <session_id> --selector "button[type=submit]"
browser-cli action type --session-id <session_id> --selector "input[name=q]" --text "hello"
browser-cli action screenshot --session-id <session_id> --output /tmp/page.png
browser-cli action eval --session-id <session_id> --script "() => document.title"
browser-cli action snapshot --session-id <session_id> --max-chars 8000
```

Each action must receive exactly one browser target:

```bash
--session-id <session_id>
--connect-url <cdp_websocket_url>
--direct-url
```

By default, action output masks `api_key` inside resolved direct connect URLs.
Use `--reveal-connect-url` only for local debugging.

Case files and compatibility aliases:

```bash
browser-cli case validate --file case.yaml
browser-cli case run --file case.yaml
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
command-specific fields.

## Suggested Agent Workflow

For a new browser task, agents should prefer this sequence:

```bash
browser-cli session create
browser-cli action open-url --session-id <session_id> --url <url>
browser-cli action snapshot --session-id <session_id>
browser-cli action click --session-id <session_id> --selector <selector>
browser-cli action type --session-id <session_id> --selector <selector> --text <text>
browser-cli action screenshot --session-id <session_id> --output /tmp/final.png
browser-cli session close --session-id <session_id>
```

Use `context create` plus `session create --context-id <context_id>` when login
state or cookies should survive between sessions.
Use `session create --resolve-context --create-context` when the agent needs a
reusable context but does not know whether one already exists or whether it is
locked by an active session. Add `--metadata-match-json` for persistent login
state tied to a specific site, account, or task. Use `context resolve` when the
agent needs to inspect the decision before starting a session.

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

1. Add a console page that shows the current `Project ID`, API key status, and
   the exact `export ...` commands for the selected project.
2. Add a scoped API key wizard for agent use, with clear permissions, optional
   expiration, and one-click revoke.
3. Provide a copyable install block:
   `uv tool install git+https://github.com/lexmount/browser-cli.git`.
4. Add a "Verify CLI" section that tells users to run
   `browser-cli session list` after setting env vars.
5. Longer term, support device-code or OAuth-style authorization so Codex can
   ask the user to approve access in the browser and then receive a local,
   short-lived token without the user manually copying API keys.
