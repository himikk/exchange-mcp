---
name: exchange-tasks
description: 'Microsoft Exchange tasks (to-do) via the exchange MCP — list, get, create, update, complete, and delete tasks. Use when the user asks to "add a task", "create a to-do", "what''s on my task list", "show my tasks", "mark task complete", "complete a task", "update a task", "delete a task", "what''s due today", "my to-do list", or any task/todo management on Exchange.'
---

# Exchange tasks

6 tools for managing tasks (to-do items) on Microsoft Exchange via EWS. Tasks live in the mailbox's default Tasks folder and track status, due dates, percent complete, reminders, and categories.

## Prerequisites

The `exchange` MCP server must be configured. If any tool fails, load the `exchange-mailbox` skill and run `ping_exchange` first.

## Status model

Exchange tasks track two related fields:

| `status` | `percent_complete` | Meaning |
|---|---|---|
| `not_started` | `0` | Not begun |
| `in_progress` | `1`–`99` | Started, not done |
| `completed` | `100` | Done (auto-sets `complete_date`) |
| `waiting_on_others` | any | Blocked on someone else |
| `deferred` | any | Postponed |

You don't set `status` directly. Instead:
- **`update_task` with `percent_complete`** — drives status automatically: 0→not_started, 1-99→in_progress, 100→completed.
- **`complete_task`** — one-shot helper: sets status=Completed, percent=100, complete_date=today.

The `status` filter on `list_tasks` and the `status` field in results are read-only views of this state.

## Tools

### `list_tasks`

| Arg | Type | Default | Notes |
|---|---|---|---|
| `limit` | int | `50` | 1–200 |
| `offset` | int | `0` | Pagination |
| `status` | `not_started`\|`in_progress`\|`completed`\|`waiting_on_others`\|`deferred` | – | Filter by status |
| `category` | string | – | Filter by category (substring match) |
| `due_before` | date | – | Tasks due before this date |
| `due_after` | date | – | Tasks due on/after this date |
| `incomplete_only` | bool | `false` | Only tasks where `is_complete` is false |

Returns `TaskSummary` items ordered by `due_date` descending (nulls last).

### `get_task`

| Arg | Type | Notes |
|---|---|---|
| `id` | string | **required** |

Returns `TaskFull` with `body`, `complete_date`, `reminder_minutes_before_start`, `companies`, `contacts`, `billing_information`, `owner`, `last_modified_time`.

### `create_task`

| Arg | Type | Default | Notes |
|---|---|---|---|
| `subject` | string | – | **required**, min 1 |
| `body` | string | – | Task body/notes |
| `body_type` | `"text"` \| `"html"` | `"text"` | |
| `start_date` | date | – | When work begins |
| `due_date` | date | – | Deadline; must be ≥ `start_date` |
| `reminder_minutes` | int | – | 0–10080; if set, enables a reminder |
| `categories` | list[string] | `[]` | |
| `importance` | `"low"`\|`"normal"`\|`"high"` | `"normal"` | |

New tasks start with `status=not_started`, `percent_complete=0`. Pass `reminder_minutes` to enable a reminder (otherwise no reminder is set).

### `update_task`

| Arg | Type | Default | Notes |
|---|---|---|---|
| `id` | string | – | **required** |
| `subject` | string \| null | – | min 1 if set |
| `body` | string \| null | – | |
| `body_type` | `"text"` \| `"html"` | `"text"` | |
| `start_date` | date \| null | – | |
| `due_date` | date \| null | – | Must remain ≥ `start_date` if both set |
| `percent_complete` | int \| null | – | 0–100; auto-transitions `status` |
| `reminder_minutes` | int \| null | – | 0–10080; enables reminder if set |
| `categories` | list[string] \| null | – | Pass `[]` to clear |
| `importance` | `"low"`\|`"normal"`\|`"high"` \| null | – | |

Only fields you pass are changed. **`percent_complete` is the primary way to move status forward** — setting it to 100 marks the task Completed; 1–99 marks InProgress (if it was NotStarted or Completed).

### `complete_task`

| Arg | Type | Notes |
|---|---|---|
| `id` | string | **required** |

One-shot convenience: sets `status=Completed`, `percent_complete=100`. Exchange auto-fills `complete_date` server-side (it's read-only). Equivalent to `update_task(percent_complete=100)` but clearer for the agent.

### `delete_task`

| Arg | Type | Default | Notes |
|---|---|---|---|
| `id` | string | – | **required** |
| `hard_delete` | bool | `false` | `false` = move to Deleted Items; `true` = permanent |

Default soft-delete is recoverable. Use `hard_delete=true` only when the user explicitly says "permanently delete" / "purge".

## Common workflows

### "What's on my task list?"

1. `list_tasks(incomplete_only=true, limit=50)` → summaries with `subject`, `due_date`, `status`, `percent_complete`.
2. For overdue items, mention the due date relative to today.
3. For any task the user wants to act on, `get_task(id=...)` for the full body/notes.

### Create a task with a due date and reminder

1. `create_task(subject="Prepare Q3 report", due_date="2026-09-30", reminder_minutes=1440, categories=["work","report"])`
2. The reminder fires 1 day (1440 min) before... actually, EWS task reminders fire at the reminder time relative to the due date.

### Make progress on a task

1. `update_task(id=..., percent_complete=50)` — moves status to `in_progress`.
2. When done: `complete_task(id=...)` or `update_task(id=..., percent_complete=100)`.

### Triage tasks by due date

1. `list_tasks(due_before="2026-08-01", incomplete_only=true)` → overdue or imminent tasks.
2. `list_tasks(status="in_progress")` → started but not finished.
3. `list_tasks(category="work")` → filtered by category.

### Delete a completed task

1. `list_tasks(status="completed")` → find the `id`.
2. `delete_task(id=...)` (soft-delete) or `delete_task(id=..., hard_delete=true)` if the user asks to purge.

## Gotchas

- **Status is derived from `percent_complete`** — you cannot set `status` directly via `update_task`. Use `percent_complete` to move it forward, or `complete_task` for the one-shot done state.
- **`waiting_on_others` and `deferred` statuses are not reachable via `update_task`** — Exchange tracks them but this MCP doesn't expose a way to set them. They appear in `list_tasks` results if set via Outlook.
- **`due_date` and `start_date` are dates, not datetimes** — pass `"2026-08-01"`, not `"2026-08-01T09:00:00"`.
- **`reminder_minutes` enables the reminder** — if you don't pass it, no reminder is set, even on create. Pass `0` to set a reminder for the due time itself.
- **Tasks live only in the default Tasks folder** — no `folder` parameter. Subfolders are not supported.
- **`complete_task` sets `complete_date` to today** (in the mailbox timezone). Don't use it to backdate completion.
- **Soft-delete moves to Deleted Items** — recoverable. `hard_delete=true` is permanent.
- **`categories` on update replaces the list** — pass `[]` to clear, not `null` (null means "don't touch").
