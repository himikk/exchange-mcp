---
name: exchange-contacts
description: Microsoft Exchange contacts via the exchange MCP — search the Global Address List (GAL) and personal contacts, get full contact details, create/update/delete personal contacts. Use when the user asks to "find a contact", "look up a colleague", "search the address book", "search GAL", "get contact details", "what's someone's email/phone", "add a contact", "create contact", "update contact", "delete contact", or any contacts task on Exchange.
---

# Exchange contacts

5 tools for searching the Global Address List (GAL) and managing personal contacts on Microsoft Exchange via EWS.

## Prerequisites

The `exchange` MCP server must be configured. If any tool fails, load the `exchange-mailbox` skill and run `ping_exchange` first.

## Two contact stores

Exchange exposes contacts from two sources, distinguished by the `source` field:

| Source | What it is | Writable? |
|---|---|---|
| `personal` | The user's own Contacts folder(s) | Yes — full CRUD |
| `gal` | Global Address List (company directory, all mailboxes) | **Read-only** — search & get only |

`search_contacts` searches either or both (default `all`). `create_contact`/`update_contact`/`delete_contact` only work on `personal` — attempting them on a GAL contact will fail.

## Tools

### `search_contacts`

| Arg | Type | Default | Notes |
|---|---|---|---|
| `query` | string | – | **required**, min length 1 |
| `source` | `"personal"` \| `"gal"` \| `"all"` | `"all"` | Which store(s) to search |
| `limit` | int | `10` | 1–100 |

Returns `ContactSummary` items: `id`, `display_name`, `email_addresses`, `phone_numbers`, `company`, `job_title`, `department`, `source`. Use `source` to decide what's possible next (GAL = read-only; personal = editable).

The `query` matches against name, email, and (for personal) company/job title. For GAL lookups by exact email, the query still works as a substring/keyword match — no special syntax.

### `get_contact`

| Arg | Type | Notes |
|---|---|---|
| `id` | string | **required** — from `search_contacts` |

Returns `ContactFull` with all fields: `first_name`, `last_name`, `email_addresses` (typed: `Email1`/`Email2`/...), `phone_numbers` (typed: `BusinessPhone`/`MobilePhone`/...), `addresses`, `company`, `job_title`, `department`, `manager`, `notes`, `photo_url`, `birthday`, `source`.

**The `id` is source-specific** — a GAL `id` only works with `get_contact`; a personal `id` also works with `update_contact`/`delete_contact`. Always check the `source` field returned by `search_contacts` before calling a write tool.

### `create_contact` — personal only

| Arg | Type | Default | Notes |
|---|---|---|---|
| `display_name` | string | – | **required**, min 1 |
| `first_name` | string | – | |
| `last_name` | string | – | |
| `email` | email | – | Single email (stored as `Email1`) |
| `phone` | string | – | Single phone (stored as `BusinessPhone`) |
| `company` | string | – | |
| `job_title` | string | – | |
| `notes` | string | – | |

Returns the new contact's `id`. For multiple emails/phones/addresses, this tool only sets one of each — use `update_contact` afterwards to add more (or extend the tool if needed).

### `update_contact` — personal only

| Arg | Type | Default | Notes |
|---|---|---|---|
| `id` | string | – | **required** |
| `display_name` | string \| null | – | min 1 if set |
| `first_name` | string \| null | – | |
| `last_name` | string \| null | – | |
| `email` | email \| null | – | Overwrites `Email1` |
| `phone` | string \| null | – | Overwrites `BusinessPhone` |
| `company` | string \| null | – | |
| `job_title` | string \| null | – | |
| `notes` | string \| null | – | |

Only fields you pass are changed; `null` means "don't touch". Setting a field to `null` explicitly is **not** supported as "clear this field" — omit it instead. To clear, pass an empty string where the field accepts one.

### `delete_contact` — personal only

| Arg | Type | Notes |
|---|---|---|
| `id` | string | **required** |

Permanent deletion from the personal Contacts folder. Not recoverable via Exchange (unlike soft-deleted emails).

## Common workflows

### Look up a colleague's contact info

1. `search_contacts(query="Jane Smith", source="gal")` → summary with `id`
2. `get_contact(id=...)` → full details (email, phone, department, manager)

### Look up someone's email before sending mail

1. `search_contacts(query="Jane Smith", source="all")` → check `email_addresses[0]`
2. If only a name match is needed, the summary is enough — no need for `get_contact`.
3. `send_email(to=[<email>], ...)` (using the `exchange-email` skill).

### Add a new personal contact

1. `create_contact(display_name="Jane Smith", first_name="Jane", last_name="Smith", email="jane@personal.example", phone="+1-555-0100", company="Acme", job_title="Engineer")`
2. If you need multiple emails/phones, follow with `update_contact` (note: the current tool overwrites `Email1`/`BusinessPhone`, not appends).

### Update a contact after a move/promotion

1. `search_contacts(query="Jane Smith", source="personal")` → `id`
2. `update_contact(id=..., job_title="Senior Engineer", company="Globex")`

### Delete a personal contact

1. `search_contacts(query="Jane Smith", source="personal")` → confirm the right `id`
2. `delete_contact(id=...)`

## Gotchas

- **GAL is read-only.** `create`/`update`/`delete` on a GAL `id` will fail. Check `source` from `search_contacts` before writing.
- **The `id` is opaque and source-scoped.** Don't assume a personal `id` works for GAL lookups or vice versa.
- **`create_contact` sets at most one email and one phone.** The schema supports multiple (typed `Email1`/`Email2`/`BusinessPhone`/`MobilePhone`/...), but this tool only populates `Email1` and `BusinessPhone`. For richer contact data, extend the server or post-process.
- **`update_contact` overwrites `Email1`/`BusinessPhone`**, it does not append. To add a second email, you'd need a different code path (not exposed by the current tool).
- **`delete_contact` is permanent** — no soft-delete, no Deleted Items recovery. Confirm with the user before deleting.
- **`search_contacts` with `source="all"` may return duplicates** — a person can appear in both GAL and personal contacts with different `id`s. Disambiguate by `source` before acting.
- **GAL search results depend on server-side resolution** — some Exchange deployments restrict which fields are visible in GAL summaries (e.g. `phone_numbers` may be empty even when `get_contact` returns them). Call `get_contact` for full fields.
