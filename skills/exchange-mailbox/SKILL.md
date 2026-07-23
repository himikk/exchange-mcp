---
name: exchange-mailbox
description: 'Microsoft Exchange mailbox health checks and metadata via the exchange MCP. Use when the user asks to "check Exchange connection", "is Exchange reachable", "ping Exchange", "get mailbox info", "what''s my mailbox size", "mailbox quota", "Exchange version", "what timezone is my mailbox", or before debugging any other exchange-mcp tool failure. Triggers: "ping exchange", "mailbox info", "mailbox size", "exchange connectivity", "is exchange up".'
---

# Exchange mailbox health & metadata

Two read-only tools for connectivity checks and mailbox metadata. Load this skill first when diagnosing any exchange-mcp failure, or when the user asks about their mailbox itself (not its contents).

## Prerequisites

The `exchange` MCP server must be configured. See the repo README for env vars (`EXCHANGE_SERVER`, `EXCHANGE_USERNAME`, `EXCHANGE_PASSWORD`, `EXCHANGE_EMAIL_ADDRESS`, `EXCHANGE_AUTH_TYPE`).

## Tools

### `ping_exchange`

Health check. No arguments. Returns `PingResult`:

| Field | Type | Notes |
|---|---|---|
| `status` | `"ok"` \| `"error"` | Overall verdict |
| `server` | string | The EWS endpoint URL |
| `version` | string \| null | Exchange server version if reachable |
| `latency_ms` | int \| null | Round-trip to EWS |
| `error` | string \| null | Error message when `status == "error"` |

**Always run this first when a user reports an exchange-mcp tool failing.** A failing ping means every other tool will fail too — fix the connection (env vars, network, credentials, `EXCHANGE_VERIFY_SSL`) before chasing per-tool errors.

### `get_mailbox_info`

Mailbox metadata. No arguments. Returns `MailboxInfo`:

| Field | Type | Notes |
|---|---|---|
| `email_address` | string | The configured mailbox |
| `display_name` | string | User's display name |
| `timezone` | string | Server-reported timezone (falls back to `EXCHANGE_TIMEZONE`, default `Europe/Moscow`) |
| `mailbox_size_mb` | float \| null | Current usage |
| `quota_mb` | float \| null | Quota, if the server reports one |
| `exchange_version` | string \| null | Server build/version |

Use this when the user asks "how big is my mailbox", "am I near quota", "what Exchange version is the server", or to confirm which mailbox the MCP is bound to (e.g. when `EXCHANGE_IMPERSONATE_AS` is set).

## Common workflows

### "Is Exchange working?"

1. Call `ping_exchange`.
2. If `status == "error"`, report `error` and suggest checking `EXCHANGE_*` env vars and `EXCHANGE_VERIFY_SSL` (self-signed certs are common on on-prem Exchange).
3. If `status == "ok"`, optionally call `get_mailbox_info` to show which mailbox is bound and its size.

### Diagnosing a failing email/calendar/contacts call

1. Call `ping_exchange` first.
2. Ping fails → connection/auth problem. Stop chasing the original tool.
3. Ping succeeds → the original tool's error is real (bad ID, missing folder, permissions, etc.). Switch to the relevant domain skill (`exchange-email`, `exchange-calendar`, `exchange-contacts`).

## Gotchas

- Both tools take no arguments. If the agent passes arguments, they are ignored.
- `ping_exchange` only verifies EWS reachability and auth — it does not validate that specific folders or calendars are accessible. A successful ping + a failing `list_emails` usually means a folder-name problem, not a connection problem.
- `mailbox_size_mb` and `quota_mb` may be `null` on some Exchange deployments that don't expose management info via EWS — don't treat null as an error.
