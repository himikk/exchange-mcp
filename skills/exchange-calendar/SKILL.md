---
name: exchange-calendar
description: Microsoft Exchange calendar via the exchange MCP — list, get, create, update, and delete events; respond to meeting invites; find free meeting slots across attendees; check availability; list calendars. Use when the user asks to "check my calendar", "what's on my schedule", "show today's events", "schedule a meeting", "create an event", "update the meeting", "cancel the event", "accept/decline/tentative invite", "find a free slot", "when are people free", "check availability", or any calendar task on Exchange.
---

# Exchange calendar

9 tools for calendar events, invites, and availability on Microsoft Exchange via EWS.

## Prerequisites

The `exchange` MCP server must be configured. If any tool fails, load the `exchange-mailbox` skill and run `ping_exchange` first.

## Timezones

All `start`/`end` arguments are ISO-8601 datetimes with explicit timezone offset (e.g. `"2025-07-23T14:00:00+03:00"`). Naive datetimes risk being interpreted in the server's timezone (which falls back to `EXCHANGE_TIMEZONE`, default `Europe/Moscow`). **Always include an offset.**

## Tools

### Listing & reading

#### `list_calendars` — list the user's calendars

No arguments. Returns `CalendarInfo` items: `id`, `name`, `is_default`, `color`, `owner_email`. Call this first when you need a `calendar_id` for the other tools, or when the user asks "which calendars do I have".

#### `list_events` — events in a time range

| Arg | Type | Default | Notes |
|---|---|---|---|
| `start` | datetime | – | **required**, ISO-8601 with offset |
| `end` | datetime | – | **required**, must be > `start` |
| `calendar_id` | string \| null | – | Defaults to primary calendar |
| `include_recurring` | bool | `true` | Expand recurring series into occurrences |

Returns `CalendarEvent` items. `end > start` is enforced.

#### `get_event` — fetch a single event by ID

| Arg | Type | Notes |
|---|---|---|
| `id` | string | **required** — from `list_events` or an invite |

Returns full `CalendarEvent` with `body`, `attendees[].response_type`, `organizer`, `online_meeting_url`, `recurrence_pattern`, `reminder_minutes`, `categories`.

### Creating & updating

#### `create_event`

| Arg | Type | Default | Notes |
|---|---|---|---|
| `subject` | string | – | **required**, min 1 |
| `start` | datetime | – | **required**, ISO-8601 with offset |
| `end` | datetime | – | **required**, > `start` |
| `calendar_id` | string \| null | – | Primary if omitted |
| `location` | string | – | |
| `body` | string | – | Event body/notes |
| `attendees` | list[email] | `[]` | Sends invites when non-empty |
| `is_all_day` | bool | `false` | See all-day note below |
| `reminder_minutes` | int | `15` | 0–10080; `0` = no reminder |
| `recurrence` | object | – | `RecurrencePattern` (see below) |
| `categories` | list[string] | `[]` | |
| `importance` | `"low"`\|`"normal"`\|`"high"` | `"normal"` | |
| `online_meeting` | bool | `false` | Create as online meeting |

Returns `CreateEventResult` with `id`, `invite_sent` (true if attendees were notified).

**All-day events:** when `is_all_day=true`, the server normalizes `start` to midnight of that date and `end` to midnight of the **following** day (end-exclusive). Don't pre-shift the times yourself — just pass the calendar date.

#### `update_event`

| Arg | Type | Default | Notes |
|---|---|---|---|
| `id` | string | – | **required** |
| `subject` | string \| null | – | |
| `start` | datetime \| null | – | |
| `end` | datetime \| null | – | Must remain > `start` if both set |
| `location` | string \| null | – | |
| `body` | string \| null | – | |
| `add_attendees` | list[email] | `[]` | Append |
| `remove_attendees` | list[email] | `[]` | Remove |
| `reminder_minutes` | int \| null | – | 0–10080 |
| `send_updates` | `"none"`\|`"all"`\|`"modified"` | `"all"` | Who gets notified |

Only fields you pass are changed. `send_updates="none"` is for silent edits; `"modified"` notifies only attendees whose copy changed (e.g. those added/removed).

### Deleting & invites

#### `delete_event`

| Arg | Type | Default | Notes |
|---|---|---|---|
| `id` | string | – | **required** |
| `notify_attendees` | bool | `true` | Send cancellation |
| `cancel_message` | string | – | Optional message to attendees |

When the user is the organizer and `notify_attendees=true`, attendees get a cancellation. As an attendee (not organizer), this removes the event from your calendar but does not notify the organizer — use `respond_to_invite` with `decline` for that.

#### `respond_to_invite` — accept / tentative / decline

| Arg | Type | Notes |
|---|---|---|
| `id` | string | **required** — the event/invite ID |
| `response` | `"accept"` \| `"tentative"` \| `"decline"` | **required** |
| `message` | string | Optional note to organizer |

Use this when the user got an invite (visible via `list_events` with `my_response == "unknown"`).

### Availability

#### `find_free_slots` — common free time across attendees

| Arg | Type | Default | Notes |
|---|---|---|---|
| `attendees` | list[email] | – | **required**, ≥1 (can include the user) |
| `duration` | int | – | **required**, meeting length in minutes, 1–1440 |
| `start` | datetime | – | **required** |
| `end` | datetime | – | **required**, > `start` |
| `work_hours` | object | – | `{start: "09:00", end: "18:00"}` window per day |

Returns `FreeSlot` items: `start`, `end`, `all_available`, `busy_attendees`. Slots are clipped to `work_hours` and exclude times where **any** attendee is busy.

#### `get_my_availability` — the user's own free/busy

| Arg | Type | Default | Notes |
|---|---|---|---|
| `start` | datetime | – | **required** |
| `end` | datetime | – | **required** |
| `calendar_id` | string \| null | – | |
| `include_recurring` | bool | `true` | |

Returns `AvailabilityResult` with `free_slots` and `busy_slots`. Use this for "am I free tomorrow afternoon" without involving other attendees.

## Common workflows

### "What's on my calendar today?"

1. `list_events(start="<today>T00:00:00+<offset>", end="<tomorrow>T00:00:00+<offset>")`
2. Summarize each event's `subject`, `start`, `end`, `location`, `attendees`, `online_meeting_url`.
3. For invites not yet responded to (`my_response == "unknown"`), offer `respond_to_invite`.

### Schedule a meeting with others

1. `find_free_slots(attendees=[...], duration=60, start="<monday>T00:00:00+<offset>", end="<friday>T00:00:00+<offset>", work_hours={start:"09:00", end:"18:00"})`
2. Pick a slot with the user.
3. `create_event(subject=..., start=<slot.start>, end=<slot.end>, attendees=[...], location=..., body=...)` — invites are sent automatically.

### Accept an invite and check for conflicts

1. `list_events(...)` → find the invite (`my_response == "unknown"`).
2. `get_my_availability(start=<event.start>, end=<event.end>)` → check `busy_slots` for conflicts.
3. `respond_to_invite(id=<event.id>, response="accept"|"tentative"|"decline", message=...)`.

### Reschedule an existing meeting

1. `find_free_slots(attendees=<existing attendees>, duration=<current duration>, ...)` for new times.
2. `update_event(id=<event.id>, start=<new.start>, end=<new.end>, send_updates="all")`.

### Cancel a meeting I organized

1. `delete_event(id=<event.id>, notify_attendees=true, cancel_message="Sorry, cancelling.")`

## Recurrence patterns

`create_event.recurrence` is a `RecurrencePattern` object:

| Field | Type | Notes |
|---|---|---|
| `type` | `"daily"` \| `"weekly"` \| `"monthly"` \| `"yearly"` | **required** |
| `interval` | int | `≥1`, default `1` (every N units) |
| `end_date` | date | Bound by date (mutually exclusive with `occurrences`) |
| `occurrences` | int | Bound by count, `≥1` |
| `days_of_week` | list[string] | For weekly: `["monday","wednesday"]` |

Either `end_date` **or** `occurrences` should be set (otherwise the series is unbounded — some Exchange versions reject this).

## Gotchas

- **Always pass timezone offsets** on datetimes. Naive datetimes are a common source of off-by-N-hours events.
- **All-day events are normalized** to midnight-to-midnight by the server. Don't try to construct that yourself; pass the date with `is_all_day=true`.
- **`update_event` with `send_updates="none"`** silently edits — fine for the organizer's own notes, but attendees won't see changes. Default is `"all"`.
- **Deleting as an attendee** removes the event from your calendar but does **not** tell the organizer. To properly decline, use `respond_to_invite(response="decline")`.
- **`find_free_slots` clips to `work_hours`** — if all attendees are busy inside work hours, you'll get an empty list, not evenings/weekends. Extend `work_hours` or widen the date range if the user wants off-hours options.
- **`get_my_availability` vs `list_events`**: availability returns aggregated free/busy slots (no event details); `list_events` returns actual event objects. Use availability for "am I free", use `list_events` for "what do I have".
- **Recurring series** — `list_events` with `include_recurring=true` expands occurrences into individual events. The `id` returned for an occurrence may be the series master or the occurrence, depending on the server — when updating a single occurrence, confirm the right ID with `get_event` first.
- **`calendar_id` is optional** and defaults to the primary calendar. Pass it explicitly when the user has multiple calendars (check via `list_calendars`).
