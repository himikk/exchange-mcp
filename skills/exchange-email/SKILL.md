---
name: exchange-email
description: Microsoft Exchange email via the exchange MCP — list, read, search, send, reply, forward, move, copy, delete, mark, and organize messages, plus folders, drafts, and attachments. Use when the user asks to "read email", "check inbox", "find an email", "search mail", "send an email", "reply to", "forward", "move to folder", "delete email", "mark as read", "flag email", "create folder", "save draft", "download attachment", "triage inbox", or any mail task on an Exchange mailbox.
---

# Exchange email

15 tools for mail on Microsoft Exchange via EWS. Covers reading, searching, sending, organizing, folders, drafts, and attachments.

## Prerequisites

The `exchange` MCP server must be configured. If any tool fails, load the `exchange-mailbox` skill and run `ping_exchange` first.

## Folder names — read this first

Many email tools take a `folder` argument. Exchange accepts **either** well-known English aliases **or** localized display names:

| English alias | Localized (RU example) |
|---|---|
| `inbox` | `Входящие` |
| `sent` | `Отправленные` |
| `drafts` | `Черновики` |
| `deleted` / `deleteditems` | `Удаленные` |
| `junk` | `Нежелательная почта` |
| `outbox` | `Исходящие` |

**Nested paths** use `/`: `inbox/jira`, `Входящие/Projects`. The path is matched by display name at each level.

When in doubt about the exact name, call `list_folders` first and use the `path` field it returns.

## Tools

### Reading & searching

#### `list_emails` — list messages in a folder

Server-side filtered listing. Returns `EmailSummary` items (not full bodies).

| Arg | Type | Default | Notes |
|---|---|---|---|
| `folder` | string | `"inbox"` | Name or path (see above) |
| `limit` | int | `20` | 1–100 |
| `offset` | int | `0` | Pagination |
| `from_address` | email | – | Server-side filter by sender |
| `subject` | string | – | Server-side substring filter |
| `since` | date | – | Inclusive |
| `before` | date | – | Exclusive |
| `unread_only` | bool | `false` | Unread only |
| `has_attachments` | bool \| null | – | `true`/`false` to filter, `null` to ignore |

Use this for inbox triage and folder browsing. For full-text search across subject+body+sender+recipients, prefer `search_emails`.

#### `get_email` — fetch a full message by ID

| Arg | Type | Notes |
|---|---|---|
| `id` | string | **required** — from `list_emails`/`search_emails` |

Returns `EmailFull` with `body_text`, `body_html`, `attachments` list, `headers`, `conversation_id`. The `id` is opaque and may change if the message is moved — re-list after moves if you need a stable handle.

#### `search_emails` — server-side full-text search (AQS)

| Arg | Type | Default | Notes |
|---|---|---|---|
| `query` | string | – | **required**, min length 1 |
| `folder` | string \| null | – | Scope to a folder; `null` searches whole mailbox |
| `limit` | int | `20` | 1–100 |

**AQS syntax** (Advanced Query Syntax). A bare term matches subject, body, sender, and recipients:

```
quarterly report
from:boss@corp.com
from:boss@corp.com AND hasattachment:true
subject:"Q3 results" AND isread:false
received:>2025-01-01
to:me@example.com AND body:contract
```

Supported keywords: `from:`, `to:`, `subject:`, `body:`, `hasattachment:` (`true`/`false`), `isread:` (`true`/`false`), `received:` (date, supports `>`/`<`/`..` ranges). Combine with `AND` / `OR` / `NOT`. Quote multi-word values.

### Sending & replying

#### `send_email` — send a new message

| Arg | Type | Default | Notes |
|---|---|---|---|
| `to` | list[email] | – | **required**, ≥1 |
| `subject` | string | – | **required**, min length 1 |
| `body` | string | – | **required** |
| `body_type` | `"text"` \| `"html"` | `"text"` | |
| `cc` | list[email] | `[]` | |
| `bcc` | list[email] | `[]` | |
| `reply_to` | email | – | Reply-to header |
| `attachments` | list[path] | `[]` | **Filesystem paths** to attach |
| `inline_images` | list[{path, content_id}] | `[]` | Inline CID images; reference in HTML body as `cid:<content_id>` |
| `importance` | `"low"` \| `"normal"` \| `"high"` | `"normal"` | |

`attachments` are local file paths the MCP server reads from disk — make sure the file is reachable by the server process, not just the agent.

`inline_images` embeds images directly in the HTML body (e.g. a signature logo). Each item is `{path: <filesystem path>, content_id: <string>}`. The HTML body must reference each image via `cid:<content_id>` (e.g. `<img src="cid:logo">`). Content type is inferred from the file extension (.png/.jpg/.jpeg/.gif/.svg/.webp). Requires `body_type="html"`. Capped by `ATTACHMENT_MAX_SIZE_MB` per image.

#### `reply_email` — reply to a message

| Arg | Type | Default | Notes |
|---|---|---|---|
| `id` | string | – | **required** |
| `body` | string | – | **required** |
| `reply_all` | bool | `false` | Include cc |
| `attachments` | list[path] | `[]` | |

#### `forward_email` — forward a message

| Arg | Type | Default | Notes |
|---|---|---|---|
| `id` | string | – | **required** |
| `to` | list[email] | – | **required**, ≥1 |
| `comment` | string | – | Prefix text above the forwarded body |
| `attachments` | list[path] | `[]` | Additional new attachments |

### Organizing

#### `move_email`

| Arg | Type | Notes |
|---|---|---|
| `id` | string | **required** |
| `folder` | string | **required** — destination name or path |

#### `copy_email` — same args as `move_email`

#### `delete_email`

| Arg | Type | Default | Notes |
|---|---|---|---|
| `id` | string | – | **required** |
| `hard_delete` | bool | `false` | `false` = move to Deleted Items (recoverable); `true` = permanent |

Default soft-delete is almost always what you want. Use `hard_delete=true` only when the user explicitly says "permanently delete" / "purge".

#### `mark_email` — update flags

| Arg | Type | Notes |
|---|---|---|
| `id` | string | **required** |
| `read` | bool \| null | `true`/`false` to set, `null` to leave |
| `flag` | `"flagged"` \| `"complete"` \| `"none"` \| null | |
| `importance` | `"low"` \| `"normal"` \| `"high"` \| null | |

Only fields you pass are changed; `null` means "don't touch". Pass `flag: "none"` to clear a flag.

### Folders

#### `list_folders`

| Arg | Type | Default | Notes |
|---|---|---|---|
| `parent` | string \| null | – | Scope to a parent; `null` = root |
| `depth` | int | `2` | 0–10; `0` = only the parent, `10` = all nested |

Returns a tree of `FolderInfo` with `id`, `name`, `path`, `unread_count`, `total_count`, `children`. Use this to discover exact folder names/paths before calling `list_emails` or `move_email`.

#### `create_folder`

| Arg | Type | Default | Notes |
|---|---|---|---|
| `name` | string | – | **required**, min length 1 |
| `parent` | string \| null | `"inbox"` | Parent path; `null` = root |

### Drafts

#### `create_draft` — save a draft without sending

Same shape as `send_email` minus `reply_to`, `importance`, `bcc` (no `bcc` on drafts). Returns the draft's `id`.

| Arg | Type | Default | Notes |
|---|---|---|---|
| `to` | list[email] | – | **required**, ≥1 |
| `subject` | string | – | **required** |
| `body` | string | – | **required** |
| `body_type` | `"text"` \| `"html"` | `"text"` | |
| `cc` | list[email] | `[]` | |
| `attachments` | list[path] | `[]` | |
| `inline_images` | list[{path, content_id}] | `[]` | Inline CID images; reference in HTML body as `cid:<content_id>` |

#### `send_draft` — send an existing draft

| Arg | Type | Notes |
|---|---|---|
| `id` | string | **required** — from `create_draft` or `list_emails(folder="drafts")` |

### Attachments

#### `get_attachment` — save an attachment to disk

| Arg | Type | Notes |
|---|---|---|
| `email_id` | string | **required** |
| `attachment_id` | string | **required** — from `get_email`'s `attachments[].id` |
| `save_path` | path \| null | If `null`, the server picks a path |

Returns `AttachmentResult` with `filename`, `size`, `saved_path`, `content_type`. The `saved_path` is on the **server's** filesystem — if the MCP server runs remotely (SSE transport), the file is not on the agent's machine.

Capped by `ATTACHMENT_MAX_SIZE_MB` (default `10`) on both send and save.

## Common workflows

### Triage unread inbox

1. `list_emails(folder="inbox", unread_only=true, limit=20)` → summaries with `id`, `subject`, `from`, `preview`, `has_attachments`
2. For each item the user cares about: `get_email(id=...)` for the full body
3. Act: `mark_email(id=..., read=true)` / `move_email(id=..., folder="inbox/action")` / `reply_email(id=..., body=...)` / `delete_email(id=...)`
4. Re-list with `offset` to page through more.

### Search + download an attachment

1. `search_emails(query="from:accounting@corp.com AND hasattachment:true")`
2. `get_email(id=<id>)` → read `attachments[].id`
3. `get_attachment(email_id=<id>, attachment_id=<att_id>, save_path="/tmp/invoice.pdf")`
4. Report `saved_path` to the user. If the server is remote (SSE), warn that the file lives on the server host.

### Reply with attachment

1. `get_email(id=...)` to confirm context
2. `reply_email(id=..., body="Here's the signed copy", reply_all=true, attachments=["/tmp/signed.pdf"])`

### Build a folder, then file messages into it

1. `create_folder(name="2025-Q3", parent="inbox/archive")`
2. For each message: `move_email(id=..., folder="inbox/archive/2025-Q3")`

### Draft for review, then send

1. `create_draft(to=[...], subject=..., body=...)` → `id`
2. User reviews/edits
3. `send_draft(id=<id>)`

## Gotchas

- **IDs are opaque and may change on move.** After `move_email`/`copy_email`, re-list if you need a current handle.
- **`from_address` on `list_emails` is server-side filtering** — use it instead of fetching and filtering client-side. But for full-text or multi-field search, use `search_emails` (AQS).
- **`attachments` on send/draft are filesystem paths read by the server**, not URLs or base64. The file must exist where the MCP process runs.
- **`get_attachment` writes to the server's filesystem.** With SSE transport this is not the agent's machine — either run stdio transport, or share a volume.
- **Soft vs hard delete:** default soft-delete moves to Deleted Items and is recoverable. Only use `hard_delete=true` when explicitly asked.
- **`mark_email` with all-`null` field values is a no-op** — don't call it without at least one concrete value.
- **Folder name mismatch** is the #1 cause of `list_emails`/`move_email` failures. Run `list_folders` first when unsure. Localized names (`Входящие`) work but must match exactly, including case.
- **`search_emails` AQS** is server-side and fast; don't substitute `list_emails` + client-side filtering for searches — it's slower and capped at `limit=100`.
