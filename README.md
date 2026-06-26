# browser-cli

`browser-cli` is a command-line interface for operating Lexmount remote browser
sessions from local shells, Codex, and other agents.

It owns command parsing, JSON output, installation docs, and agent-facing
ergonomics. Browser lifecycle and page action behavior stay in
`lex-browser-runtime`, so this project does not maintain a second copy of the
runtime implementation.

## Codex Install Prompt

Copy this prompt into Codex when you want Codex to install and configure the
CLI for you. Keep API keys and Project IDs in your local shell; do not paste
them into chat.

```text
请帮我安装并配置 Lexmount browser-cli，用于在 Codex 中操作 Lexmount 远程浏览器。

约束：
1. 不要让我把 API Key 或 Project ID 粘贴到聊天里。
2. 不要在聊天回复、日志、README、测试、提交记录或 PR 描述里输出 API Key。
3. 所有 secret 只能让我在本机 shell 或本机 shell 配置文件中处理。
4. 如果命令输出中出现 masked/revealed/contains_secrets/usable 字段，请按这些字段判断是否可以复制到聊天里；revealed secret 永远不要复制到聊天。
5. 除非我明确要求，否则不要创建、提交或推送任何包含凭据的文件。

步骤：
1. 检查本机是否已经安装 uv：
   uv --version
2. 如果没有 uv，提示我先安装 uv：
   curl -LsSf https://astral.sh/uv/install.sh | sh
3. 安装 browser-cli：
   uv tool install git+https://github.com/lexmount/browser-cli.git
4. 确认 CLI 可用：
   browser-cli --help
5. 运行本地认证状态检查，并解析 JSON：
   browser-cli auth status
6. 如果 auth status 显示 missing 包含 LEXMOUNT_API_KEY 或 LEXMOUNT_PROJECT_ID，运行：
   browser-cli auth login
7. 引导我打开 https://browser.lexmount.cn 并登录账号。
8. 引导我在 browser.lexmount.cn 中选择要给 Codex 使用的 Project。
9. 引导我复制该 Project 的 Project ID，并创建或复制一个给 agent/browser automation 使用的 API Key。
10. 引导我只在本机 shell 中设置：
   export LEXMOUNT_API_KEY="<从 browser.lexmount.cn 获取的 API Key>"
   export LEXMOUNT_PROJECT_ID="<从 browser.lexmount.cn 获取的 Project ID>"
11. 可以运行下面命令生成 shell 配置片段，但默认输出是 masked，不能直接当作可用凭据：
   browser-cli auth export-env
12. 只有在本机可信 shell 中需要可直接执行的 export 行时，才让我自己运行：
   browser-cli auth export-env --reveal-secrets
   并提醒我不要把该输出粘贴到聊天里。
13. 告诉我中国区默认会使用 https://api.lexmount.cn，通常不需要设置 LEXMOUNT_BASE_URL。
14. 如果我希望长期保存配置，引导我把这些 export 写入当前 shell 配置文件，例如 ~/.zshrc 或 ~/.bashrc。
15. 运行下面命令验证：
   browser-cli --help
   browser-cli --version
   browser-cli auth status
   browser-cli direct-url
   browser-cli session list
16. 如果验证失败，请按顺序排查：
   - uv 是否可用
   - browser-cli 是否在 PATH 中
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
browser-cli --version
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

Auth helpers:

```bash
browser-cli auth status
browser-cli auth login
browser-cli auth export-env
browser-cli auth export-env --reveal-secrets
browser-cli auth export-env --shell fish
browser-cli auth export-env --shell powershell
```

`auth status` reports whether the required env vars are configured without
printing API keys by default. `auth login` gives browser.lexmount.cn onboarding
steps. `auth export-env` returns JSON containing shell lines; they are masked by
default, and become directly usable only with `--reveal-secrets` in a trusted
local shell.

## Commands

Auth and credential guidance:

```bash
browser-cli auth status
browser-cli auth login
browser-cli auth export-env
browser-cli auth export-env --include-base-url --reveal-secrets
```

Session management:

```bash
browser-cli session create
browser-cli session create --create-context
browser-cli session create --context-id <context_id> --context-mode read_write
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
browser-cli auth status
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
6. Once that flow exists, wire `browser-cli auth login` to start the
   device-code/OAuth authorization instead of only returning manual guidance.
