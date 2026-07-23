# exchange-mcp

MCP server for Microsoft Exchange access via EWS

## Install

From PyPI:

```bash
pip install exchange-mcp
# or
uv add exchange-mcp
```

From source:

```bash
pip install .
# or
uv pip install .
```

## Configure

Set these environment variables (see `.env.example` for the full list):

```bash
export EXCHANGE_SERVER=https://mail.company.com/EWS/Exchange.asmx
export EXCHANGE_USERNAME="DOMAIN\\username"
export EXCHANGE_PASSWORD=secret
export EXCHANGE_EMAIL_ADDRESS=user@company.com
export EXCHANGE_AUTH_TYPE=NTLM   # NTLM (default) or Basic; OAuth2 is not wired yet
```

Optional settings:

| Variable | Default | Purpose |
|---|---|---|
| `EXCHANGE_VERIFY_SSL` | `true` | Set `false` for self-signed certs |
| `EXCHANGE_VERSION` | autodetect | Pin server version, e.g. `EXCHANGE_2016` |
| `EXCHANGE_TIMEOUT` / `EXCHANGE_MAX_RETRIES` | `30` / `3` | Request timeout (s) / retry budget |
| `EXCHANGE_TIMEZONE` | `Europe/Moscow` | Fallback for unknown server timezone ids |
| `EXCHANGE_IMPERSONATE_AS` | – | Impersonate another mailbox (needs rights) |
| `ATTACHMENT_MAX_SIZE_MB` | `10` | Size cap for sending and saving attachments |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `sse` |
| `MCP_SSE_HOST` / `MCP_SSE_PORT` | `127.0.0.1` / `8080` | SSE bind address |
| `LOG_LEVEL` / `LOG_FILE` | `INFO` / stderr | Logging |

## Tools

31 tools: email (list/get/search/send/reply/forward/move/copy/delete/mark, folders, drafts, attachments), calendar (list/get/create/update/delete events, respond to invites, find free slots, availability), contacts (search incl. GAL, get/create/update/delete), plus `ping_exchange`, `get_mailbox_info`, `list_calendars`.

`search_emails` runs server-side full-text search (AQS): a bare term matches subject, body, sender and recipients; keywords like `from:`, `subject:`, `hasattachment:`, `isread:`, `received:` are supported. `list_emails` also filters server-side by sender via `from_address`.

## Develop

```bash
pip install -e ".[dev]"
pytest
```

## Claude Code

```json
{
  "mcpServers": {
    "exchange": {
      "command": "exchange-mcp",
      "env": {
        "EXCHANGE_SERVER": "https://mail.company.com/EWS/Exchange.asmx",
        "EXCHANGE_USERNAME": "DOMAIN\\\\username",
        "EXCHANGE_PASSWORD": "secret",
        "EXCHANGE_EMAIL_ADDRESS": "user@company.com",
        "EXCHANGE_AUTH_TYPE": "NTLM"
      }
    }
  }
}
```
