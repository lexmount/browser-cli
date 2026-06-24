# browser-cli

Standalone CLI for operating Lexmount browser sessions.

The CLI surface is extracted from `website-skills/lex_browser_runtime/cli.py`.
Runtime behavior stays in the `lex-browser-runtime` package, so this repository
owns command parsing and packaging while avoiding a second copy of browser
session/action implementation logic.

## Install

```bash
pip install browser-cli
```

## Codex install prompt

Copy the following prompt into the Codex client to install and configure this
browser operation skill:

```text
请帮我安装并配置 Lexmount browser-cli，用于在 Codex 中操作 Lexmount 远程浏览器。

要求：
1. 不要让我把 API Key 或 Project ID 粘贴到聊天里；只指导我在本机 shell 中设置环境变量。
2. 先检查本机是否已经安装 uv：
   - 如果已有 uv，继续下一步。
   - 如果没有 uv，提示我先安装 uv，并给出官方安装命令。
3. 使用 uv 安装 browser-cli：
   uv tool install git+https://github.com/lexmount/browser-cli.git
4. 引导我打开 https://browser.lexmount.cn 登录账号。
5. 引导我在 browser.lexmount.cn 控制台中找到 API Key 和 Project ID。
6. 引导我在本机 shell 中设置下面的环境变量：
   export LEXMOUNT_API_KEY="<从 browser.lexmount.cn 获取的 API Key>"
   export LEXMOUNT_PROJECT_ID="<从 browser.lexmount.cn 获取的 Project ID>"
   export LEXMOUNT_BASE_URL="https://api.lexmount.cn"
7. 如果我希望长期保存配置，引导我把这些 export 写入当前 shell 的配置文件，例如 ~/.zshrc 或 ~/.bashrc。
8. 安装完成后，运行下面命令验证：
   browser-cli --help
   browser-cli direct-url
9. 如果验证失败，请根据错误信息排查：
   - uv 是否可用
   - browser-cli 是否在 PATH 中
   - LEXMOUNT_API_KEY 是否已设置
   - LEXMOUNT_PROJECT_ID 是否已设置
   - LEXMOUNT_BASE_URL 是否正确

完成后告诉我：
- browser-cli 的安装路径
- 验证命令是否通过
- 我当前还需要手动做什么
```

## Development

This project uses `uv` for local Python dependency management and command
execution.

```bash
uv sync --all-groups
uv run browser-cli --help
uv run pytest tests -q
uv run ruff format --check .
uv run ruff check .
```

## Commands

```bash
browser-cli session create
browser-cli session list
browser-cli session get --session-id <session_id>
browser-cli session close --session-id <session_id>
browser-cli context list
browser-cli action open-url --session-id <session_id> --url https://example.com
browser-cli action snapshot --session-id <session_id>
browser-cli case validate --file case.yaml
browser-cli case run --file case.yaml
browser-cli direct-url
```

The CLI reads Lexmount credentials from the same environment variables used by
`lex-browser-runtime`:

- `LEXMOUNT_API_KEY`
- `LEXMOUNT_PROJECT_ID`
- `LEXMOUNT_BASE_URL`
- `LEXMOUNT_REGION`
